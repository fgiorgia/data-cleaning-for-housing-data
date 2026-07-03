#!/usr/bin/env python3
"""
Geocoding CLI tool for Nashville Housing Data

This tool geocodes addresses from the unique_addresses table using
a hybrid approach with OpenStreetMap and HERE APIs.
"""

import argparse
import time
import sys
import logging
from typing import Optional, List, Dict, Any, cast
from datetime import datetime
from sqlalchemy import text
from geocoding_service import GeocodingService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("geocoding_cli.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("geocoding_cli")

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Geocode addresses from the unique_addresses table")
    
    parser.add_argument(
        "--batch-size", 
        type=int, 
        default=100,
        help="Number of addresses to process in each batch (default: 100)"
    )
    
    parser.add_argument(
        "--limit", 
        type=int, 
        default=None,
        help="Maximum number of addresses to process (default: all)"
    )
    
    parser.add_argument(
        "--address-type", 
        choices=["property", "owner", "both"],
        default="both",
        help="Type of address to geocode (default: both)"
    )
    
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="Show what would be done without making any changes"
    )
    
    parser.add_argument(
        "--force", 
        action="store_true",
        help="Force re-geocoding of addresses that already have coordinates"
    )
    
    parser.add_argument(
        "--stats-only", 
        action="store_true",
        help="Show current geocoding stats without geocoding anything"
    )
    
    return parser.parse_args()

def show_stats(service: GeocodingService) -> None:
    """Show geocoding statistics."""
    api_stats = service.get_api_usage_stats()
    
    print("\n===== API Usage Statistics =====")
    print(f"TODAY'S USAGE:")
    print(f"  HERE API: {api_stats['today_usage'].get('HERE', 0)} / 1000 requests")
    print(f"  OSM API: {api_stats['today_usage'].get('OSM', 0)} requests")
    print(f"  HERE API remaining: {api_stats['here_daily_remaining']} requests")
    
    print("\n===== Database Statistics =====")
    try:
        with service.engine.connect() as conn:
            # Try a direct query instead of using the view which might not exist
            total_count = conn.execute(text("SELECT COUNT(*) FROM unique_addresses")).scalar()
            geocoded_count = conn.execute(text("""
                SELECT COUNT(*) FROM unique_addresses 
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            """)).scalar()
            not_geocoded_count = total_count - geocoded_count
            osm_count = conn.execute(text("""
                SELECT COUNT(*) FROM unique_addresses WHERE source = 'OSM'
            """)).scalar() or 0
            here_count = conn.execute(text("""
                SELECT COUNT(*) FROM unique_addresses WHERE source = 'HERE'
            """)).scalar() or 0
            
            # Calculate percentage
            geocoded_percentage = (geocoded_count / total_count * 100) if total_count > 0 else 0
            
            print(f"Total addresses: {total_count}")
            print(f"Geocoded: {geocoded_count} ({geocoded_percentage:.2f}%)")
            print(f"Not geocoded: {not_geocoded_count}")
            print(f"OSM: {osm_count}")
            print(f"HERE: {here_count}")
            
            # Get recent geocoding results
            result = conn.execute(text("""
                SELECT source, status, COUNT(*) as count
                FROM unique_addresses
                WHERE source IS NOT NULL AND status IS NOT NULL
                GROUP BY source, status
                ORDER BY source, status
            """)).fetchall()
            
            print("\n===== Geocoding Results =====")
            for row in result:
                # Access by numerical index, as we know the columns are source, status, count
                print(f"{row[0]} - {row[1]}: {row[2]}")
    except Exception as e:
        print(f"Error getting database statistics: {e}")

def geocode_addresses(args: argparse.Namespace) -> None:
    """Geocode addresses based on command line arguments."""
    service = GeocodingService()
    
    if args.stats_only:
        show_stats(service)
        return
    
    print(f"Starting geocoding process...")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Address type: {args.address_type}")
    print(f"  Maximum addresses: {args.limit if args.limit else 'all'}")
    print(f"  Dry run: {args.dry_run}")
    print(f"  Force re-geocoding: {args.force}")
    
    # Show current API usage
    api_stats = service.get_api_usage_stats()
    print(f"\nHERE API today: {api_stats['today_usage'].get('HERE', 0)} / 1000 requests")
    print(f"HERE API remaining: {api_stats['here_daily_remaining']} requests")
    
    # Check if we're running low on HERE API requests
    if api_stats['here_daily_remaining'] < 100:
        print(f"\nWARNING: Only {api_stats['here_daily_remaining']} HERE API requests remaining!")
        if not args.dry_run:
            confirm = input("Continue anyway? (y/n): ")
            if confirm.lower() != 'y':
                print("Geocoding aborted.")
                return
    
    # Get addresses to geocode from the unique_addresses table
    where_clauses: List[str] = []
    
    if not args.force:
        where_clauses.append("(latitude IS NULL OR longitude IS NULL)")
    
    # For address type filtering, we need to join with address_mappings
    if args.address_type == "property":
        query = """
            SELECT DISTINCT ua.address_id
            FROM unique_addresses ua
            JOIN address_mappings am ON ua.address_id = am.address_id
            WHERE am.address_type = 'property'
            """
        if where_clauses:
            query += f" AND {' AND '.join(where_clauses)}"
    elif args.address_type == "owner":
        query = """
            SELECT DISTINCT ua.address_id 
            FROM unique_addresses ua
            JOIN address_mappings am ON ua.address_id = am.address_id
            WHERE am.address_type = 'owner'
            """
        if where_clauses:
            query += f" AND {' AND '.join(where_clauses)}"
    else:  # both
        query = """
            SELECT DISTINCT ua.address_id, ua.address_standardized 
            FROM unique_addresses ua
            """
        if where_clauses:
            query += f" WHERE {' AND '.join(where_clauses)}"
    
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    query += f" {limit_clause}"
    
    with service.engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()
    
    address_ids = [row[0] for row in rows]
    total = len(address_ids)
    print(f"\nFound {total} addresses to geocode")
    
    if args.dry_run:
        print("Dry run - no addresses will be geocoded")
        return
    
    if total == 0:
        print("No addresses to geocode")
        return
    
    # Process addresses in batches
    start_time = time.time()
    successful = 0
    processed = 0
    
    for i in range(0, total, args.batch_size):
        batch = address_ids[i:i+args.batch_size]
        batch_successful = 0
        batch_time = time.time()
        
        print(f"\nProcessing batch {i//args.batch_size + 1}/{(total-1)//args.batch_size + 1} ({len(batch)} addresses)")
        
        for address_id in batch:
            try:
                # Get the full address for display
                with service.engine.connect() as conn:
                    address_info = conn.execute(text("""
                        SELECT address_standardized , city FROM unique_addresses WHERE address_id = :address_id
                    """), {"address_id": address_id}).fetchone()
                
                if address_info:
                    address_standardized , city = address_info
                    full_address = f"{address_standardized }, {city}" if city else address_standardized 
                    print(f"  Geocoding [{address_id}]: {full_address}")
                else:
                    print(f"  Geocoding address_id: {address_id}")
                    continue  # Skip geocoding if address info is not found
            except Exception as e:
                logger.error(f"Error fetching address_standardized info for ID {address_id}: {e}")
                print(f"    Error fetching address_standardized: {e}")
                continue  # Skip this address and move to the next one
                
            try:
                # Geocode the address
                result = service.geocode_address(address_id)
                processed += 1
                
                if result.get("from_cache"):
                    print(f"    Found in cache: {result.get('source')} - {result.get('status')}")
                else:
                    print(f"    {result.get('source')}: {result.get('status')}")
                
                # Geocoding is automatically saved to unique_addresses by the geocode_address method
                if result.get("status") == "GEOCODED":
                    latitude = result.get("latitude")
                    longitude = result.get("longitude")
                    source = result.get("source")
                    
                    print(f"    Location: ({latitude}, {longitude})")
                    
                    # Store was successful
                    successful += 1
                    batch_successful += 1
                    
                    # If you want to propagate the geocoding results to the housing_data table too,
                    # uncomment this section:
                    """
                    # Propagate to all housing records that use this address
                    with service.engine.begin() as conn:
                        # Find all housing records that use this address
                        conn.execute(text('''
                            UPDATE housing_data hd
                            SET latitude = :latitude,
                                longitude = :longitude,
                                geocoding_source = :source,
                                geocoded_at = NOW()
                            FROM address_mappings am
                            WHERE hd.unique_id = am.housing_id
                            AND am.address_id = :address_id
                        '''), {
                            "latitude": latitude,
                            "longitude": longitude,
                            "source": source,
                            "address_id": address_id
                        })
                    """
                
            except Exception as e:
                logger.error(f"Error geocoding address_id {address_id}: {e}")
                print(f"    Error: {e}")
            
            # Brief pause between requests
            time.sleep(0.2)
        
        # Log batch progress
        batch_time = time.time() - batch_time
        print(f"\nBatch complete: {batch_successful}/{len(batch)} successful ({i+len(batch)}/{total} total)")
        print(f"Batch time: {batch_time:.1f} seconds ({batch_time/len(batch):.1f} seconds per address)")
        
        # Show updated API stats
        api_stats = service.get_api_usage_stats()
        here_used = api_stats['today_usage'].get('HERE', 0)
        print(f"HERE API usage: {here_used}/1000 ({api_stats['here_daily_remaining']} remaining)")
        
        # Check if we're running low on HERE API requests
        if api_stats['here_daily_remaining'] < 50:
            print(f"\nWARNING: Only {api_stats['here_daily_remaining']} HERE API requests remaining!")
            confirm = input("Continue processing? (y/n): ")
            if confirm.lower() != 'y':
                print("Geocoding stopped by user.")
                break
    
    # Show final statistics
    total_time = time.time() - start_time
    minutes = int(total_time // 60)
    seconds = total_time % 60
    
    print("\n===== Geocoding Summary =====")
    print(f"Processed {processed}/{total} addresses")
    if processed > 0:  # Avoid division by zero
        print(f"Successfully geocoded: {successful} ({successful/processed*100:.1f}% success rate)")
    else:
        print(f"Successfully geocoded: {successful} (0.0% success rate)")
    print(f"Total time: {minutes} minutes {seconds:.1f} seconds")
    if processed > 0:  # Avoid division by zero
        print(f"Average time per address: {total_time/processed:.1f} seconds")
    
    # Show final API stats
    show_stats(service)

if __name__ == "__main__":
    args = parse_args()
    try:
        geocode_addresses(args)
    except KeyboardInterrupt:
        print("\nGeocode process interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.exception("Geocoding process failed")
        print(f"Error: {e}")
        sys.exit(1)