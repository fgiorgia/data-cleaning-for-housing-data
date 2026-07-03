import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
from sqlalchemy import create_engine
from typing import Dict, List, Optional, Tuple, Any
import os

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Database connection
def get_db_connection():
    """Create a database connection"""
    hostname = os.environ.get("DB_HOSTNAME", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    database = os.environ.get("DB_DATABASE", "postgres")
    username = os.environ.get("DB_USERNAME", "postgres")
    password = os.environ.get("DB_PASSWORD", "")
    
    connection_string = f"postgresql://{username}:{password}@{hostname}:{port}/{database}"
    return create_engine(connection_string)

# Data loading functions
def load_housing_data() -> pd.DataFrame:
    """Load the housing data from the database"""
    engine = get_db_connection()
    return pd.read_sql("SELECT * FROM housing_data", engine)

def load_column_info() -> pd.DataFrame:
    """Load column metadata from the database"""
    engine = get_db_connection()
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
        if np.issubdtype(dtype, np.number):
            min_val = df[col].min()
            max_val = df[col].max()
            mean_val = df[col].mean()
            zeros = (df[col] == 0).sum()
            negatives = (df[col] < 0).sum()
            
            stat = {
                'column': col,
                'data_type': str(dtype),
                'missing': missing,
                'missing_pct': missing_pct,
                'unique_values': unique_count,
                'unique_pct': unique_pct,
                'min': min_val,
                'max': max_val,
                'mean': mean_val,
                'zeros': zeros,
                'negatives': negatives
            }
        else:
            # For non-numeric columns
            stat = {
                'column': col,
                'data_type': str(dtype),
                'missing': missing,
                'missing_pct': missing_pct,
                'unique_values': unique_count,
                'unique_pct': unique_pct,
                'min': None,
                'max': None,
                'mean': None,
                'zeros': None,
                'negatives': None
            }
            
            # Additional text-specific stats if it's a string column
            if df[col].dtype == 'object':
                # Count empty strings
                empty_strings = (df[col] == '').sum()
                stat['empty_strings'] = empty_strings
                
                # Check for standardized formats (if applicable)
                if 'date' in col.lower():
                    # Try to parse as date and count failures
                    try:
                        date_conversion_failures = pd.to_datetime(df[col], errors='coerce').isna().sum() - missing
                        stat['date_format_issues'] = date_conversion_failures
                    except:
                        stat['date_format_issues'] = None
                
                if 'address' in col.lower():
                    # Check for address patterns (simple check for comma presence)
                    comma_missing = df[col].fillna('').apply(lambda x: ',' not in str(x)).sum()
                    stat['address_format_issues'] = comma_missing
        
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
        avg_missing = quality_stats['missing_pct'].mean()
        st.metric("Avg. Missing Values", f"{avg_missing:.2f}%")
    with col4:
        duplicate_count = len(df) - df.drop_duplicates().shape[0]
        st.metric("Duplicate Records", f"{duplicate_count:,}")
    
    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Missing Values", "Data Distribution", "Address Quality"])
    
    with tab1:
        st.header("Data Overview")
        
        # Column info table
        st.subheader("Column Information")
        st.dataframe(column_info)
        
        # Quality stats summary
        st.subheader("Data Quality Statistics")
        st.dataframe(quality_stats)
        
        # Sample data
        st.subheader("Sample Records")
        st.dataframe(df.head(10))
    
    with tab2:
        st.header("Missing Values Analysis")
        
        # Missing values heatmap
        st.subheader("Missing Values Heatmap")
        
        # Prepare missing value data for heatmap
        missing_df = pd.DataFrame({
            'column': quality_stats['column'],
            'missing_pct': quality_stats['missing_pct']
        }).sort_values('missing_pct', ascending=False)
        
        fig = px.bar(
            missing_df, 
            x='column', 
            y='missing_pct',
            color='missing_pct',
            color_continuous_scale='Reds',
            title='Percentage of Missing Values by Column'
        )
        fig.update_layout(xaxis_title="Column", yaxis_title="Missing Values (%)")
        st.plotly_chart(fig, use_container_width=True)
        
        # Missing values correlation
        st.subheader("Missing Values Correlation")
        st.write("This shows if missing values in one column correlate with missing values in another column")
        
        # Create a binary DataFrame for missing values
        missing_matrix = df.isna().astype(int)
        corr_matrix = missing_matrix.corr()
        
        fig = px.imshow(
            corr_matrix,
            color_continuous_scale='RdBu_r',
            title='Correlation Between Missing Values'
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        st.header("Data Distribution Analysis")
        
        # Select column for distribution analysis
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
        
        col_type = st.radio("Column Type", ["Numeric", "Categorical"])
        
        if col_type == "Numeric":
            selected_col = st.selectbox("Select Column", numeric_cols)
            
            # Distribution plot
            fig = px.histogram(
                df, 
                x=selected_col,
                marginal='box',
                title=f'Distribution of {selected_col}'
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Summary statistics
            st.subheader("Summary Statistics")
            stats_df = pd.DataFrame({
                'Metric': ['Count', 'Mean', 'Median', 'Std Dev', 'Min', 'Max', 'Missing'],
                'Value': [
                    df[selected_col].count(),
                    df[selected_col].mean(),
                    df[selected_col].median(),
                    df[selected_col].std(),
                    df[selected_col].min(),
                    df[selected_col].max(),
                    df[selected_col].isna().sum()
                ]
            })
            st.dataframe(stats_df)
            
            # Outlier detection
            st.subheader("Outlier Detection")
            q1 = df[selected_col].quantile(0.25)
            q3 = df[selected_col].quantile(0.75)
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            outliers = df[(df[selected_col] < lower_bound) | (df[selected_col] > upper_bound)]
            
            st.write(f"IQR Method Outlier Count: {len(outliers)}")
            if len(outliers) > 0:
                st.dataframe(outliers.head(10))
        
        else:  # Categorical
            selected_col = st.selectbox("Select Column", categorical_cols)
            
            # Value counts
            value_counts = df[selected_col].value_counts().reset_index()
            value_counts.columns = [selected_col, 'Count']
            
            # Limit to top 20 values for readability
            if len(value_counts) > 20:
                value_counts = value_counts.head(20)
                st.info("Showing top 20 values only")
            
            fig = px.bar(
                value_counts,
                x=selected_col,
                y='Count',
                title=f'Value Counts for {selected_col}',
                color='Count',
                color_continuous_scale='Viridis'
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Frequency table
            st.subheader("Frequency Table")
            st.dataframe(value_counts)
    
    with tab4:
        st.header("Address Quality Analysis")
        
        # Filter to address-related columns
        address_cols = [col for col in df.columns if 'address' in col.lower()]
        
        if not address_cols:
            st.warning("No address columns found in the dataset")
        else:
            selected_addr_col = st.selectbox("Select Address Column", address_cols)
            
            # Address completeness
            st.subheader("Address Completeness")
            
            # Basic checks
            has_comma = df[selected_addr_col].fillna('').str.contains(',').sum()
            comma_pct = has_comma / len(df) * 100
            
            has_numbers = df[selected_addr_col].fillna('').str.contains(r'\d').sum()
            numbers_pct = has_numbers / len(df) * 100
            
            # Display metrics
            addr_col1, addr_col2, addr_col3 = st.columns(3)
            with addr_col1:
                st.metric("Addresses with Commas", f"{comma_pct:.1f}%")
            with addr_col2:
                st.metric("Addresses with Numbers", f"{numbers_pct:.1f}%")
            with addr_col3:
                missing = df[selected_addr_col].isna().sum()
                missing_pct = missing / len(df) * 100
                st.metric("Missing Addresses", f"{missing_pct:.1f}%")
            
            # Address length distribution
            address_lengths = df[selected_addr_col].fillna('').str.len()
            
            fig = px.histogram(
                address_lengths,
                title='Address Length Distribution',
                labels={'value': 'Character Count', 'count': 'Frequency'}
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Sample addresses
            st.subheader("Sample Addresses")
            
            # Show a mix of potentially problematic and normal addresses
            short_addresses = df[df[selected_addr_col].fillna('').str.len() < 10]
            no_comma_addresses = df[~df[selected_addr_col].fillna('').str.contains(',')]
            normal_addresses = df[
                (df[selected_addr_col].fillna('').str.len() >= 10) &
                (df[selected_addr_col].fillna('').str.contains(','))
            ]
            
            st.write("Short Addresses (potentially incomplete):")
            st.dataframe(short_addresses[['unique_id', selected_addr_col]].head(5))
            
            st.write("Addresses Without Commas (potentially missing city/state/zip):")
            st.dataframe(no_comma_addresses[['unique_id', selected_addr_col]].head(5))
            
            st.write("Regular Addresses:")
            st.dataframe(normal_addresses[['unique_id', selected_addr_col]].head(5))

if __name__ == "__main__":
    render_dashboard()