import argparse
import sys
from datetime import datetime

import folium
import pandas as pd
from folium.plugins import MarkerCluster
from sqlalchemy import text

from scripts.config import get_db_config
from scripts.db import get_engine


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

        # Add a marker cluster for better performance with many markers
        marker_cluster = MarkerCluster().add_to(m)

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

        # Define a function to color markers based on price
        def get_price_color(price: float) -> str:
            if price < 200000:
                return "green"
            elif price < 400000:
                return "blue"
            elif price < 600000:
                return "orange"
            else:
                return "red"

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

            # Add marker to map with color based on price
            folium.Marker(
                location=[row["latitude"], row["longitude"]],
                popup=folium.Popup(popup_content, max_width=300),
                tooltip=f"${row['sale_price']:,.0f}",
                icon=folium.Icon(color=get_price_color(row["sale_price"])),
            ).add_to(marker_cluster)

        # Add a legend for price ranges
        legend_html = """
        <div style="position: fixed; 
                    bottom: 50px; right: 50px; width: 180px; height: 120px; 
                    border:2px solid grey; z-index:9999; font-size:12px;
                    background-color:white; padding: 10px;
                    border-radius:5px;">
        <div style="font-weight: bold; margin-bottom: 5px;">Price Range</div>
        <div><i class="fa fa-circle" style="color:green"></i> Under $200,000</div>
        <div><i class="fa fa-circle" style="color:blue"></i> $200,000 - $399,999</div>
        <div><i class="fa fa-circle" style="color:orange"></i> $400,000 - $599,999</div>
        <div><i class="fa fa-circle" style="color:red"></i> $600,000+</div>
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
