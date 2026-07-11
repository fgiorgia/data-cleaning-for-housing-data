from typing import cast

import pandas as pd
import plotly.express as px
import streamlit as st

from scripts.db import get_engine

# A sale recorded twice: same parcel, address, price, date and legal
# reference. This is the same duplicate definition src/cleaning.sql uses.
# Deduplicating on all columns would always report 0, because unique_id is
# a primary key and makes every full row distinct by construction.
DUPLICATE_BUSINESS_KEY: list[str] = [
    "parcel_id",
    "property_address",
    "sale_price",
    "sale_date",
    "legal_reference",
]


# Data loading functions. Streamlit reruns the whole script on every widget
# interaction; without caching each click would re-read the full table.
@st.cache_data(ttl=600)
def load_housing_data() -> pd.DataFrame:
    """Load the housing data from the database (cached for 10 minutes)"""
    engine = get_engine()
    return pd.read_sql("SELECT * FROM housing_data", engine)


@st.cache_data(ttl=600)
def load_quality_issue_summary() -> pd.DataFrame | None:
    """Counts per issue_type from data_quality_issues, if the table exists.

    The cleaning-pipeline database has no such table; returning None lets
    the dashboard simply skip the section there.
    """
    engine = get_engine()
    try:
        return pd.read_sql(
            """
            SELECT issue_type, count(*) AS records
            FROM data_quality_issues
            GROUP BY issue_type
            ORDER BY records DESC
            """,
            engine,
        )
    except Exception:
        return None


@st.cache_data(ttl=600)
def load_column_info() -> pd.DataFrame:
    """Load column metadata from the database (cached for 10 minutes)"""
    engine = get_engine()
    query = """
    SELECT 
        column_name, 
        data_type,
        is_nullable
    FROM 
        information_schema.columns
    WHERE 
        table_name = 'housing_data'
    ORDER BY 
        ordinal_position
    """
    return pd.read_sql(query, engine)


def get_data_quality_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate data quality statistics for each column"""
    stats = []

    for col in df.columns:
        # Basic stats
        missing = df[col].isna().sum()
        missing_pct = missing / len(df) * 100
        unique_count = df[col].nunique()
        unique_pct = unique_count / len(df) * 100

        # Data type specific stats
        dtype = df[col].dtype
        if pd.api.types.is_numeric_dtype(df[col]):
            min_val = df[col].min()
            max_val = df[col].max()
            mean_val = df[col].mean()
            zeros = (df[col] == 0).sum()
            negatives = (df[col] < 0).sum()

            stat = {
                "column": col,
                "data_type": str(dtype),
                "missing": missing,
                "missing_pct": missing_pct,
                "unique_values": unique_count,
                "unique_pct": unique_pct,
                "min": min_val,
                "max": max_val,
                "mean": mean_val,
                "zeros": zeros,
                "negatives": negatives,
            }
        else:
            # For non-numeric columns
            stat = {
                "column": col,
                "data_type": str(dtype),
                "missing": missing,
                "missing_pct": missing_pct,
                "unique_values": unique_count,
                "unique_pct": unique_pct,
                "min": None,
                "max": None,
                "mean": None,
                "zeros": None,
                "negatives": None,
            }

            # Additional text-specific stats if it's a string column
            if df[col].dtype == "object":
                # Count empty strings
                empty_strings = (df[col] == "").sum()
                stat["empty_strings"] = empty_strings

                # Check for standardized formats (if applicable)
                if "date" in col.lower():
                    # Try to parse as date and count failures
                    try:
                        date_conversion_failures = (
                            pd.to_datetime(df[col], errors="coerce").isna().sum()
                            - missing
                        )
                        stat["date_format_issues"] = date_conversion_failures
                    except ValueError, TypeError:
                        stat["date_format_issues"] = None

                if "address" in col.lower():
                    # An address without a digit has no house number. Checked
                    # over non-null values only: a missing address is already
                    # counted in `missing`, not a format issue on top of it.
                    non_null = df[col].dropna().astype(str)
                    stat["no_house_number"] = int((~non_null.str.contains(r"\d")).sum())

        stats.append(stat)

    return pd.DataFrame(stats)


# Dashboard UI
def render_dashboard():
    """Render the Streamlit dashboard"""
    st.set_page_config(page_title="Housing Data Quality Dashboard", layout="wide")

    st.title("Housing Data Quality Dashboard")
    st.write("Interactive dashboard for analyzing housing data quality")

    # Load data
    with st.spinner("Loading data..."):
        df = load_housing_data()
        column_info = load_column_info()
        quality_stats = get_data_quality_stats(df)

    # Dashboard metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Records", f"{len(df):,}")
    with col2:
        st.metric("Total Columns", f"{len(df.columns):,}")
    with col3:
        avg_missing = quality_stats["missing_pct"].mean()
        st.metric(
            "Missing Cells",
            f"{avg_missing:.2f}%",
            help="Share of all table cells that are NULL "
            "(mean of the per-column missing percentages).",
        )
    with col4:
        duplicate_key = [c for c in DUPLICATE_BUSINESS_KEY if c in df.columns]
        if not duplicate_key:
            duplicate_key = list(df.columns)
        duplicate_count = int(df.duplicated(subset=duplicate_key).sum())
        st.metric(
            "Duplicate Sale Records",
            f"{duplicate_count:,}",
            help="Rows sharing the same " + ", ".join(duplicate_key) + ". "
            "The unique_id primary key is deliberately excluded - including "
            "it would make every row distinct and always report 0.",
        )

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Overview", "Missing Values", "Data Distribution", "Address Quality"]
    )

    with tab1:
        st.header("Data Overview")

        # Column info table
        st.subheader("Column Information")
        st.dataframe(column_info)

        # Quality stats summary
        st.subheader("Data Quality Statistics")
        st.dataframe(quality_stats)

        # Imputation provenance: values filled in during cleaning rather
        # than present in the source data (see src/address_imputation.sql).
        imputed_flag_cols = [c for c in df.columns if c.endswith("_imputed")]
        if imputed_flag_cols:
            st.subheader("Imputation Provenance")
            st.write(
                "Rows whose value was derived during cleaning instead of "
                "coming from the source data. Filter these out when an "
                "analysis must use source values only."
            )
            prov_columns = st.columns(len(imputed_flag_cols))
            for ui_col, flag_col in zip(prov_columns, imputed_flag_cols):
                with ui_col:
                    imputed_count = int(df[flag_col].sum())
                    st.metric(
                        flag_col,
                        f"{imputed_count:,}",
                        help=f"{imputed_count / len(df) * 100:.2f}% of rows",
                    )

        # Curated issue log maintained in the database itself (populated by
        # the enrichment process and src/data_quality_maintenance.sql).
        issue_summary = load_quality_issue_summary()
        if issue_summary is not None and not issue_summary.empty:
            st.subheader("Recorded Data Quality Issues")
            st.write(
                "Issues flagged in the `data_quality_issues` table - the "
                "curated, reviewable log, as opposed to the statistics "
                "computed on the fly above."
            )
            st.dataframe(issue_summary)

        # Sample data
        st.subheader("Sample Records")
        st.dataframe(df.head(10))

    with tab2:
        st.header("Missing Values Analysis")

        # Missing values heatmap
        st.subheader("Missing Values Heatmap")

        # Prepare missing value data for heatmap
        missing_df = pd.DataFrame(
            {
                "column": quality_stats["column"],
                "missing_pct": quality_stats["missing_pct"],
            }
        ).sort_values("missing_pct", ascending=False)

        fig = px.bar(
            missing_df,
            x="column",
            y="missing_pct",
            color="missing_pct",
            color_continuous_scale="Reds",
            title="Percentage of Missing Values by Column",
        )
        fig.update_layout(xaxis_title="Column", yaxis_title="Missing Values (%)")
        st.plotly_chart(fig, use_container_width=True)

        # Missing values correlation
        st.subheader("Missing Values Correlation")
        st.write(
            "This shows if missing values in one column correlate with missing values in another column"
        )

        # Only columns that actually have missing values: a column with no
        # NULLs has zero variance, which makes its correlations undefined
        # (NaN) and fills the heatmap with blank rows.
        cols_with_missing = [c for c in df.columns if df[c].isna().any()]
        if len(cols_with_missing) < 2:
            st.info(
                "Fewer than two columns have missing values - "
                "there is nothing to correlate."
            )
        else:
            missing_matrix = df[cols_with_missing].isna().astype(int)
            corr_matrix = missing_matrix.corr()

            fig = px.imshow(
                corr_matrix,
                color_continuous_scale="RdBu_r",
                zmin=-1,
                zmax=1,
                title="Correlation Between Missing Values",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Columns whose missing masks are near-identical form a block:
            # those rows were never enriched with that whole group of fields
            # (a structural gap), which reads very differently from
            # scattered per-field problems.
            st.subheader("Columns That Go Missing Together")
            ordered = df[cols_with_missing].isna().sum().sort_values(ascending=False)
            blocks: list[list[str]] = []
            for col in ordered.index:
                for block in blocks:
                    if cast(float, corr_matrix.loc[col, block[0]]) >= 0.95:
                        block.append(col)
                        break
                else:
                    blocks.append([col])
            block_rows = [
                {
                    "columns": ", ".join(block),
                    "column_count": len(block),
                    "rows_missing_entire_block": int(
                        df[block].isna().all(axis=1).sum()
                    ),
                    "pct_of_rows": round(
                        df[block].isna().all(axis=1).sum() / len(df) * 100, 1
                    ),
                }
                for block in blocks
                if len(block) > 1
            ]
            if block_rows:
                st.dataframe(pd.DataFrame(block_rows))
            else:
                st.info("No group of columns shares a missing-value pattern.")

    with tab3:
        st.header("Data Distribution Analysis")

        # Select column for distribution analysis. Booleans (sold_as_vacant,
        # the *_imputed flags) behave like two-value categoricals here.
        numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
        categorical_cols = df.select_dtypes(
            include=["object", "bool", "boolean", "category"]
        ).columns.tolist()

        col_type = st.radio("Column Type", ["Numeric", "Categorical"])

        if col_type == "Numeric":
            selected_col = st.selectbox("Select Column", numeric_cols)

            # Distribution plot
            fig = px.histogram(
                df,
                x=selected_col,
                marginal="box",
                title=f"Distribution of {selected_col}",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Summary statistics
            st.subheader("Summary Statistics")
            # Percentiles alongside the mean: sale prices and land values
            # are heavily right-skewed, so the mean alone misleads.
            stats_df = pd.DataFrame(
                {
                    "Metric": [
                        "Count",
                        "Mean",
                        "Std Dev",
                        "Min",
                        "25th Pctl",
                        "Median",
                        "75th Pctl",
                        "95th Pctl",
                        "Max",
                        "Missing",
                    ],
                    "Value": [
                        df[selected_col].count(),
                        df[selected_col].mean(),
                        df[selected_col].std(),
                        df[selected_col].min(),
                        df[selected_col].quantile(0.25),
                        df[selected_col].median(),
                        df[selected_col].quantile(0.75),
                        df[selected_col].quantile(0.95),
                        df[selected_col].max(),
                        df[selected_col].isna().sum(),
                    ],
                }
            )
            st.dataframe(stats_df)

            # Outlier detection
            st.subheader("Outlier Detection")
            q1 = df[selected_col].quantile(0.25)
            q3 = df[selected_col].quantile(0.75)
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            outliers = df[
                (df[selected_col] < lower_bound) | (df[selected_col] > upper_bound)
            ]

            st.write(f"IQR Method Outlier Count: {len(outliers)}")
            if len(outliers) > 0:
                st.dataframe(outliers.head(10))

        else:  # Categorical
            selected_col = st.selectbox("Select Column", categorical_cols)

            # Value counts, with missing shown as its own category instead
            # of silently dropped (NULL and a value are different facts).
            value_counts = (
                df[selected_col]
                .astype("string")
                .fillna("(missing)")
                .value_counts()
                .reset_index()
            )
            value_counts.columns = [selected_col, "Count"]

            # Limit to top 20 values for readability
            if len(value_counts) > 20:
                value_counts = value_counts.head(20)
                st.info("Showing top 20 values only")

            fig = px.bar(
                value_counts,
                x=selected_col,
                y="Count",
                title=f"Value Counts for {selected_col}",
                color="Count",
                color_continuous_scale="Viridis",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Frequency table
            st.subheader("Frequency Table")
            st.dataframe(value_counts)

    with tab4:
        st.header("Address Quality Analysis")

        # Filter to address-related columns
        address_cols = [col for col in df.columns if "address" in col.lower()]

        if not address_cols:
            st.warning("No address columns found in the dataset")
        else:
            selected_addr_col = st.selectbox("Select Address Column", address_cols)

            # Address completeness
            st.subheader("Address Completeness")

            # Format checks run over NON-NULL addresses only: a missing
            # address is a missingness fact (shown as its own metric), not a
            # format problem stacked on top of it.
            addresses = df[selected_addr_col].dropna().astype(str)
            non_null_count = len(addresses)

            has_numbers = addresses.str.contains(r"\d").sum()
            numbers_pct = has_numbers / non_null_count * 100 if non_null_count else 0.0

            # "Street, City" in one field is only expected when the schema
            # has no companion city column. This database stores city
            # separately (property_city / owner_city), where a comma check
            # would flag 100% of rows as broken.
            city_col = selected_addr_col.replace("address", "city")
            if city_col in df.columns:
                with_city = df.loc[df[selected_addr_col].notna(), city_col].notna()
                second_label = f"City Present ({city_col})"
                second_pct = (
                    with_city.sum() / non_null_count * 100 if non_null_count else 0.0
                )
                second_help = (
                    f"Share of non-missing addresses whose {city_col} is filled."
                )
            else:
                has_comma = addresses.str.contains(",").sum()
                second_label = "Addresses with Commas"
                second_pct = has_comma / non_null_count * 100 if non_null_count else 0.0
                second_help = "Street and city share this field, separated by a comma."

            # A leading house number of 0 is a placeholder, not a location:
            # geocoders resolve it to a street centroid, so the coordinates
            # look valid but are wrong.
            placeholder_mask = addresses.str.match(r"0( |$)")
            placeholder_count = int(placeholder_mask.sum())

            # Display metrics
            addr_col1, addr_col2, addr_col3, addr_col4 = st.columns(4)
            with addr_col1:
                st.metric(
                    "Addresses with House Number",
                    f"{numbers_pct:.1f}%",
                    help="Share of non-missing addresses containing a digit.",
                )
            with addr_col2:
                st.metric(second_label, f"{second_pct:.1f}%", help=second_help)
            with addr_col3:
                st.metric(
                    "Placeholder House Number (0)",
                    f"{placeholder_count:,}",
                    help="Addresses starting with house number 0. These "
                    "geocode to a street centroid, not a real location.",
                )
            with addr_col4:
                missing = df[selected_addr_col].isna().sum()
                missing_pct = missing / len(df) * 100
                st.metric("Missing Addresses", f"{missing_pct:.1f}%")

            # Address length distribution, non-null only: filling NULLs with
            # "" would add a fake spike at zero length.
            fig = px.histogram(
                addresses.str.len(),
                title="Address Length Distribution (non-missing addresses)",
                labels={"value": "Character Count", "count": "Frequency"},
            )
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

            # Sample addresses
            st.subheader("Sample Addresses")

            # Show a mix of potentially problematic and normal addresses,
            # excluding missing ones (they have no format to inspect).
            present = df[df[selected_addr_col].notna()].copy()
            addr_text = present[selected_addr_col].astype(str)
            is_short = addr_text.str.len() < 10
            has_digit = addr_text.str.contains(r"\d")

            st.write("Short Addresses (potentially incomplete):")
            st.dataframe(
                present.loc[is_short, ["unique_id", selected_addr_col]].head(5)
            )

            st.write("Addresses Without a House Number:")
            st.dataframe(
                present.loc[~has_digit, ["unique_id", selected_addr_col]].head(5)
            )

            st.write("Placeholder House Number (0):")
            st.dataframe(
                present.loc[
                    addr_text.str.match(r"0( |$)"),
                    ["unique_id", selected_addr_col],
                ].head(5)
            )

            st.write("Regular Addresses:")
            st.dataframe(
                present.loc[
                    ~is_short & has_digit, ["unique_id", selected_addr_col]
                ].head(5)
            )


if __name__ == "__main__":
    render_dashboard()
