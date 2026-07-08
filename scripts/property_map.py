import argparse
import json
import math
import sys
from bisect import bisect_right
from datetime import datetime

import folium
import pandas as pd
from folium.plugins import MarkerCluster
from sqlalchemy import text

from scripts.config import get_db_config
from scripts.db import get_engine

# Sequential warm ramp, light -> dark = cheap -> expensive, so marker color
# is ordered like the value it encodes. Each entry is (awesome-markers pin
# name, that pin's fill hex, readable text color on that fill); the hexes are
# reused for cluster icons and the legend so every layer matches the pins.
PRICE_BUCKETS: list[tuple[str, str, str]] = [
    ("beige", "#FFCB92", "#303030"),
    ("orange", "#F69730", "#303030"),
    ("red", "#D63E2A", "#FFFFFF"),
    ("darkred", "#A23336", "#FFFFFF"),
]


def nice_round(value: float) -> float:
    """Round to 2 significant digits so breakpoints read cleanly in the legend."""
    if value <= 0:
        return 0.0
    magnitude = 10 ** (math.floor(math.log10(value)) - 1)
    return float(round(value / magnitude) * magnitude)


def compute_price_breaks(prices: "pd.Series[float]") -> list[float]:
    """Quartile breakpoints from the actual data: four equal-count buckets.

    Derived from the dataset rather than hardcoded so the ranges stay
    meaningful whatever the price distribution is (a $2k-$50k dataset gets
    $2k-$50k buckets, not everything under the lowest fixed threshold).
    """
    quartiles = [float(prices.quantile(q)) for q in (0.25, 0.5, 0.75)]
    breaks = [nice_round(q) for q in quartiles]
    # Rounding can collapse near-equal quartiles; fall back to exact values.
    if len(set(breaks)) < 3:
        breaks = quartiles
    return sorted(breaks)


def price_bucket(price: float, breaks: list[float]) -> int:
    """Index into PRICE_BUCKETS; a price equal to a breakpoint goes up."""
    return bisect_right(breaks, price)


def main() -> None:
    # Initialize parser
    parser = argparse.ArgumentParser(
        description="Generate an interactive property map from Nashville housing data"
    )

    # Get database config from environment
    db_config = get_db_config()

    # Adding arguments with environment defaults
    parser.add_argument(
        "--hostname",
        default=db_config["hostname"],
        help=f"Database hostname (default: {db_config['hostname']})",
    )
    parser.add_argument(
        "--port",
        default=db_config["port"],
        help=f"Database port (default: {db_config['port']})",
    )
    parser.add_argument(
        "--username",
        default=db_config["username"],
        help=f"Database username (default: {db_config['username']})",
    )
    parser.add_argument(
        "--database",
        default=db_config["database"],
        help=f"Database name (default: {db_config['database']})",
    )
    parser.add_argument(
        "--output",
        default="nashville_property_map.html",
        help="Output HTML file path (default: nashville_property_map.html)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=60000,
        help="Maximum number of properties to show (default: 60000)",
    )

    # Read arguments from command line
    args = parser.parse_args()

    create_property_map(args)


def create_property_map(args: argparse.Namespace) -> None:
    # Get database password from environment
    db_config = get_db_config()
    password = db_config["password"]

    if not password:
        print("Error: Database password not found in environment variables.")
        sys.exit(1)

    # scripts.db pins the psycopg (v3) dialect and escapes special
    # characters in the password (the old f-string URL broke on both).
    engine = get_engine(
        {
            "hostname": args.hostname,
            "port": args.port,
            "database": args.database,
            "username": args.username,
            "password": password,
        }
    )

    # Check if the required tables exist
    check_query = """
    SELECT 
        EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'housing_data') AS housing_data_exists,
        EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'address_mappings') AS address_mappings_exists, 
        EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'unique_addresses') AS unique_addresses_exists
    """

    try:
        with engine.connect() as conn:
            result = conn.execute(text(check_query)).fetchone()
            housing_data_exists, address_mappings_exists, unique_addresses_exists = (
                result
            )

        if not (
            housing_data_exists and address_mappings_exists and unique_addresses_exists
        ):
            missing_tables = []
            if not housing_data_exists:
                missing_tables.append("housing_data")
            if not address_mappings_exists:
                missing_tables.append("address_mappings")
            if not unique_addresses_exists:
                missing_tables.append("unique_addresses")

            print(f"Error: Required tables missing: {', '.join(missing_tables)}.")
            sys.exit(1)

        # Fetch property data with coordinates by joining the tables
        print("Fetching property data from database...")

        query = f"""
        SELECT DISTINCT ON (hd.unique_id) 
            hd.unique_id, 
            hd.property_address,
            ua.address_standardized,
            hd.sale_price_numeric AS sale_price,
            hd.sale_date,
            hd.acreage,
            hd.bedrooms,
            hd.full_bath,
            hd.half_bath,
            hd.year_built,
            hd.land_value,
            hd.building_value,
            hd.total_value,
            ua.latitude,
            ua.longitude
        FROM housing_data hd
        JOIN address_mappings am ON hd.unique_id = am.housing_id
        JOIN unique_addresses ua ON am.address_id = ua.address_id
        WHERE am.address_type = 'property'
          AND ua.latitude IS NOT NULL 
          AND ua.longitude IS NOT NULL
          AND hd.sale_price_numeric IS NOT NULL
        ORDER BY hd.unique_id, hd.sale_price_numeric DESC
        LIMIT {args.limit}
        """

        properties_df = pd.read_sql(text(query), engine)

        record_count = len(properties_df)
        print(f"Fetched {record_count} properties with geocoding data")

        if record_count == 0:
            print(
                "No geocoded properties found with sale prices. Please check your data or geocoding results."
            )
            sys.exit(1)

        # Calculate center of the map (average of all properties)
        center_lat = properties_df["latitude"].mean()
        center_lng = properties_df["longitude"].mean()

        # Create a map centered on the data
        m = folium.Map(location=[center_lat, center_lng], zoom_start=12)

        # Price buckets come from the data's own quartiles, not fixed dollar
        # thresholds (see compute_price_breaks).
        price_breaks = compute_price_breaks(properties_df["sale_price"])

        # Add a marker cluster for better performance with many markers.
        # Leaflet's default cluster icon is colored by marker COUNT (green =
        # few markers), which reads as "cheap" next to the price-colored pins
        # - a $600k+ street showed green until zoomed in. Color clusters by
        # the median price of their children instead, on the same ramp, with
        # a border and dark-on-light count text as contrast relief for the
        # lightest step.
        icon_create_function = f"""
        function(cluster) {{
            var breaks = {json.dumps(price_breaks)};
            var colors = {json.dumps([hex for _, hex, _ in PRICE_BUCKETS])};
            var textColors = {json.dumps([tc for _, _, tc in PRICE_BUCKETS])};
            var prices = [];
            cluster.getAllChildMarkers().forEach(function(marker) {{
                if (marker.options && typeof marker.options.price === 'number') {{
                    prices.push(marker.options.price);
                }}
            }});
            prices.sort(function(a, b) {{ return a - b; }});
            var n = prices.length;
            var median = n === 0 ? 0 : (n % 2 === 1
                ? prices[(n - 1) / 2]
                : (prices[n / 2 - 1] + prices[n / 2]) / 2);
            var i = 0;
            while (i < breaks.length && median >= breaks[i]) {{ i++; }}
            return L.divIcon({{
                html: '<div style="background:' + colors[i] + ';color:' + textColors[i]
                    + ';border:2px solid rgba(0,0,0,0.35);border-radius:50%;'
                    + 'width:36px;height:36px;line-height:32px;text-align:center;'
                    + 'font:bold 12px Arial;box-shadow:0 0 0 4px ' + colors[i] + '55;">'
                    + cluster.getChildCount() + '</div>',
                className: '',
                iconSize: L.point(36, 36)
            }});
        }}
        """
        marker_cluster = MarkerCluster(
            icon_create_function=icon_create_function
        ).add_to(m)

        # Add information about the dataset
        price_min = properties_df["sale_price"].min()
        price_max = properties_df["sale_price"].max()
        price_avg = properties_df["sale_price"].mean()

        folium.Marker(
            location=[center_lat, center_lng],
            popup=folium.Popup(
                f"""
            <div style="
                position: absolute;
                top: 10px;
                right: 10px;
                z-index: 1000;
                background-color: white;
                padding: 10px;
                border-radius: 5px;
                border: 1px solid #ccc;
                box-shadow: 0 0 5px rgba(0,0,0,0.2);
                font-family: Arial;
                color: black;
                font-size: 12px;
                max-width: 180px;
            ">
                <b style="font-size: 14px;">Nashville Housing Data</b><br>
                Properties: {record_count}<br>
                Price Range: ${price_min:,.0f} - ${price_max:,.0f}<br>
                Average Price: ${price_avg:,.0f}
            </div>
            """,
                max_width=300,
            ),
        ).add_to(m)

        def get_price_color(price: float) -> str:
            """Pin color from the data-driven bucket the price falls in."""
            return PRICE_BUCKETS[price_bucket(price, price_breaks)][0]

        # Add markers for each property
        for idx, row in properties_df.iterrows():
            # Format sale date
            sale_date = row["sale_date"]
            if pd.notna(sale_date):
                sale_date_str = pd.to_datetime(sale_date).strftime("%b %d, %Y")
            else:
                sale_date_str = "Unknown"

            # Use standardized_address if available, otherwise fall back to property_address
            display_address = (
                row["address_standardized"]
                if pd.notna(row["address_standardized"])
                else row["property_address"]
            )

            # Format numeric values with proper handling for NaN values
            bedrooms_str = (
                str(int(row["bedrooms"])) if pd.notna(row["bedrooms"]) else "N/A"
            )

            # Calculate bathrooms correctly
            full_bath = row["full_bath"] if pd.notna(row["full_bath"]) else 0
            half_bath = row["half_bath"] if pd.notna(row["half_bath"]) else 0
            bathrooms = full_bath + (half_bath * 0.5)
            bathrooms_str = str(bathrooms) if bathrooms > 0 else "N/A"

            # Format year built
            year_built_str = (
                str(int(row["year_built"])) if pd.notna(row["year_built"]) else "N/A"
            )

            # Format acreage
            acreage_str = f"{row['acreage']:.2f}" if pd.notna(row["acreage"]) else "N/A"

            # Format total value - this fixes the error
            total_value_str = (
                f"${row['total_value']:,.0f}" if pd.notna(row["total_value"]) else "N/A"
            )

            # Create popup content with HTML formatting
            popup_content = f"""
            <div style="font-family: Arial; width: 200px;">
                <h4 style="margin-bottom: 5px;">${row["sale_price"]:,.0f}</h4>
                <div style="font-size: 12px; margin-bottom: 5px;">
                    {display_address}
                </div>
                <table style="font-size: 11px; width: 100%;">
                    <tr>
                        <td><b>Sale Date:</b></td>
                        <td>{sale_date_str}</td>
                    </tr>
                    <tr>
                        <td><b>Bedrooms:</b></td>
                        <td>{bedrooms_str}</td>
                    </tr>
                    <tr>
                        <td><b>Bathrooms:</b></td>
                        <td>{bathrooms_str}</td>
                    </tr>
                    <tr>
                        <td><b>Year Built:</b></td>
                        <td>{year_built_str}</td>
                    </tr>
                    <tr>
                        <td><b>Acreage:</b></td>
                        <td>{acreage_str}</td>
                    </tr>
                    <tr>
                        <td><b>Property Value:</b></td>
                        <td>{total_value_str}</td>
                    </tr>
                </table>
            </div>
            """

            # Add marker to map with color based on price. The extra `price`
            # option rides along on the Leaflet marker so the cluster's
            # icon_create_function can aggregate its children's prices.
            folium.Marker(
                location=[row["latitude"], row["longitude"]],
                popup=folium.Popup(popup_content, max_width=300),
                tooltip=f"${row['sale_price']:,.0f}",
                icon=folium.Icon(color=get_price_color(row["sale_price"])),
                price=float(row["sale_price"]),
            ).add_to(marker_cluster)

        # Add a legend for price ranges, built from the same breakpoints and
        # hexes the pins and clusters use.
        bucket_labels = [
            f"Under ${price_breaks[0]:,.0f}",
            f"${price_breaks[0]:,.0f} - ${price_breaks[1]:,.0f}",
            f"${price_breaks[1]:,.0f} - ${price_breaks[2]:,.0f}",
            f"${price_breaks[2]:,.0f}+",
        ]
        legend_rows = "\n".join(
            f'<div style="margin: 2px 0;">'
            f'<span style="display: inline-block; width: 12px; height: 12px;'
            f" background: {hex_color}; border: 1px solid rgba(0,0,0,0.35);"
            f' border-radius: 50%; margin-right: 6px; vertical-align: -1px;"></span>'
            f"{label}</div>"
            for (_, hex_color, _), label in zip(PRICE_BUCKETS, bucket_labels)
        )
        legend_html = f"""
        <div style="position: fixed;
                    bottom: 50px; right: 50px; width: 200px;
                    border: 2px solid grey; z-index: 9999; font-size: 12px;
                    font-family: Arial; color: #303030;
                    background-color: white; padding: 10px;
                    border-radius: 5px;">
        <div style="font-weight: bold; margin-bottom: 5px;">Sale Price</div>
        {legend_rows}
        <div style="margin-top: 6px; font-size: 11px; color: #575757;">
        Clusters: color = median price of the grouped homes, number = how many
        </div>
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))

        # Save the map
        m.save(args.output)
        print(f"Property map saved to {args.output}")

    except Exception as e:
        print(f"Database error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    start_time = datetime.now()
    print(f"Started at {start_time}")

    try:
        main()
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

    end_time = datetime.now()
    print(f"Finished at {end_time}")
    print(f"Total runtime: {end_time - start_time}")
