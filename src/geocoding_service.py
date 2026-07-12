import hashlib
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests
from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    inspect,
    text,
)

from scripts.config import get_db_config
from scripts.db import get_engine
from scripts.nashville_bounds import is_within_nashville_bounds

# Nominatim usage policy: identify the application and stay <= 1 req/s.
# https://operations.osmfoundation.org/policies/nominatim/
USER_AGENT: str = (
    "nashville-housing-cleaning/0.1 "
    "(https://github.com/fgiorgia/data-cleaning-for-housing-data)"
)
NOMINATIM_MIN_INTERVAL_S: float = 1.1

# HERE's free quota is a rolling window: the first HERE call opens a 24h
# window and the counter starts fresh 24h after that call - not at midnight.
HERE_WINDOW: timedelta = timedelta(hours=24)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("geocoding.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class GeocodingService:
    """
    A hybrid geocoding service that tries OpenStreetMap first and falls back to HERE.
    """

    def __init__(self) -> None:
        """Initialize the geocoding service."""
        self._last_osm_call: float = 0.0

        # Get API keys from environment variables
        self.here_api_key = os.environ.get("HERE_API_KEY")

        # Database connection
        self.db_config = get_db_config()
        self.engine = get_engine(self.db_config)

        # Create necessary tables if they don't exist
        self._create_tables()

        # Define API endpoints
        self.osm_endpoint = "https://nominatim.openstreetmap.org/search"
        self.here_endpoint = "https://geocode.search.hereapi.com/v1/geocode"

        # Initialize counters for API usage
        self._init_api_usage_counter()

    def _create_tables(self) -> None:
        """Validate and create any missing tables for geocoding data."""
        # Check if required tables exist
        inspector = inspect(self.engine)
        existing_tables = inspector.get_table_names()

        with self.engine.begin() as conn:
            # Ensure unique_addresses table exists and has required columns
            if "unique_addresses" in existing_tables:
                # Check for required columns and add them if missing
                columns = [
                    col["name"] for col in inspector.get_columns("unique_addresses")
                ]

                if "address_hash" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE unique_addresses ADD COLUMN address_hash VARCHAR(32)"
                        )
                    )
                    logger.info("Added address_hash column to unique_addresses")

                if "latitude" not in columns:
                    conn.execute(
                        text("ALTER TABLE unique_addresses ADD COLUMN latitude FLOAT")
                    )
                    logger.info("Added latitude column to unique_addresses")

                if "longitude" not in columns:
                    conn.execute(
                        text("ALTER TABLE unique_addresses ADD COLUMN longitude FLOAT")
                    )
                    logger.info("Added longitude column to unique_addresses")

                if "source" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE unique_addresses ADD COLUMN source VARCHAR(10)"
                        )
                    )
                    logger.info("Added source column to unique_addresses")

                if "status" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE unique_addresses ADD COLUMN status VARCHAR(20)"
                        )
                    )
                    logger.info("Added status column to unique_addresses")

                if "confidence" not in columns:
                    conn.execute(
                        text("ALTER TABLE unique_addresses ADD COLUMN confidence FLOAT")
                    )
                    logger.info("Added confidence column to unique_addresses")

                if "corrected_address" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE unique_addresses ADD COLUMN corrected_address TEXT"
                        )
                    )
                    logger.info("Added corrected_address column to unique_addresses")

                if "geocoded_at" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE unique_addresses ADD COLUMN geocoded_at TIMESTAMP"
                        )
                    )
                    logger.info("Added geocoded_at column to unique_addresses")

                if "last_updated" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE unique_addresses ADD COLUMN last_updated TIMESTAMP"
                        )
                    )
                    logger.info("Added last_updated column to unique_addresses")
            else:
                logger.error(
                    "unique_addresses table doesn't exist. Please run the setup script first."
                )
                raise Exception("unique_addresses table not found")

            # Create API usage table if it doesn't exist
            if "api_usage" not in existing_tables:
                metadata = MetaData()
                api_usage = Table(
                    "api_usage",
                    metadata,
                    Column("id", Integer, primary_key=True),
                    Column("api_name", String, nullable=False),
                    Column("request_date", Date, nullable=False),
                    Column("request_count", Integer, default=0),
                    Column("last_updated", DateTime),
                    # Start of the rolling 24h quota window (HERE rows only;
                    # NULL for OSM, whose counter is informational per-day).
                    Column("window_started_at", DateTime),
                    # Required by every INSERT ... ON CONFLICT (api_name, request_date)
                    UniqueConstraint(
                        "api_name",
                        "request_date",
                        name="uq_api_usage_api_name_request_date",
                    ),
                )
                api_usage.create(conn)
                logger.info("Created api_usage table")
                # Seeding today's rows happens in _init_api_usage_counter().
            else:
                # Databases created before the rolling-window change lack the
                # window anchor column.
                conn.execute(
                    text(
                        "ALTER TABLE api_usage "
                        "ADD COLUMN IF NOT EXISTS window_started_at TIMESTAMP"
                    )
                )

            # Create address correction log if it doesn't exist
            if "address_correction_log" not in existing_tables:
                metadata = MetaData()
                address_correction_log = Table(
                    "address_correction_log",
                    metadata,
                    Column("id", Integer, primary_key=True),
                    Column("address_id", Integer, nullable=False),
                    Column("original_value", String),
                    Column("new_value", String),
                    Column("field_changed", String),
                    Column("changed_by", String),
                    Column("changed_at", DateTime),
                    Column("reason", String),
                )
                address_correction_log.create(conn)
                logger.info("Created address_correction_log table")

            # Create spatial column if PostGIS is available
            try:
                conn.execute(text("""
                    CREATE EXTENSION IF NOT EXISTS postgis;
                    ALTER TABLE unique_addresses ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);
                    CREATE INDEX IF NOT EXISTS idx_unique_addresses_geom ON unique_addresses USING GIST(geom);
                """))
                logger.info("Set up PostGIS extension and geometry column")
            except Exception as e:
                logger.warning(f"Could not set up PostGIS extension: {e}")

    def _init_api_usage_counter(self) -> None:
        """Load the persistent API usage counters.

        OSM keeps an informational per-day row. HERE uses a rolling 24h
        window: one api_usage row per window, anchored at the first HERE
        call via window_started_at. No HERE row is seeded here - the next
        HERE call opens (and anchors) the window.
        """
        today: date = date.today()
        # engine.begin() commits; the old engine.connect() rolled the INSERT
        # back on exit, so today's row never existed and no usage persisted.
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO api_usage (api_name, request_date, request_count, last_updated)
                VALUES ('OSM', :today, 0, NOW())
                ON CONFLICT (api_name, request_date) DO NOTHING
            """),
                {"today": today},
            )
            # Rows written before the rolling-window change have no anchor;
            # treat them as windows opened at their date's local midnight,
            # which matches the old resets-at-midnight semantics.
            conn.execute(text("""
                UPDATE api_usage SET window_started_at = request_date::timestamp
                WHERE api_name = 'HERE' AND window_started_at IS NULL
            """))
            row = conn.execute(text("""
                SELECT request_count, window_started_at FROM api_usage
                WHERE api_name = 'HERE'
                ORDER BY window_started_at DESC
                LIMIT 1
            """)).fetchone()
        self._usage_date: date = today
        self.here_daily_requests: int = 0
        self._here_window_started_at: datetime | None = None
        if row is not None and datetime.now() < row[1] + HERE_WINDOW:
            self.here_daily_requests = int(row[0])
            self._here_window_started_at = row[1]
            logger.info(
                f"HERE API usage for the 24h window opened "
                f"{row[1]:%Y-%m-%d %H:%M}: {self.here_daily_requests} requests"
            )
        else:
            logger.info("HERE API: no active 24h window - the next HERE call opens one")

    def _increment_api_usage(self, api_name: str) -> None:
        """Increment the persistent counter; the DB value is authoritative."""
        now: datetime = datetime.now()
        with self.engine.begin() as conn:
            if api_name == "HERE":
                if (
                    self._here_window_started_at is None
                    or now >= self._here_window_started_at + HERE_WINDOW
                ):
                    # First call of a new window: this timestamp anchors the
                    # 24h quota. ON CONFLICT covers the (rare) case where a
                    # row for today already exists - keep its anchor.
                    row = conn.execute(
                        text("""
                        INSERT INTO api_usage (api_name, request_date, request_count, last_updated, window_started_at)
                        VALUES ('HERE', :today, 1, NOW(), :now)
                        ON CONFLICT (api_name, request_date)
                        DO UPDATE SET request_count = api_usage.request_count + 1,
                                    last_updated = NOW()
                        RETURNING request_count, window_started_at
                    """),
                        {"today": now.date(), "now": now},
                    ).fetchone()
                else:
                    row = conn.execute(
                        text("""
                        UPDATE api_usage
                        SET request_count = request_count + 1, last_updated = NOW()
                        WHERE api_name = 'HERE' AND window_started_at = :window_start
                        RETURNING request_count, window_started_at
                    """),
                        {"window_start": self._here_window_started_at},
                    ).fetchone()
                if row is not None:
                    self.here_daily_requests = int(row[0])
                    self._here_window_started_at = row[1]
                logger.info(
                    f"Incremented HERE API usage counter. "
                    f"New count: {self.here_daily_requests}"
                )
            else:
                conn.execute(
                    text("""
                    INSERT INTO api_usage (api_name, request_date, request_count, last_updated)
                    VALUES (:api, :today, 1, NOW())
                    ON CONFLICT (api_name, request_date)
                    DO UPDATE SET request_count = api_usage.request_count + 1,
                                last_updated = NOW()
                """),
                    {"api": api_name, "today": now.date()},
                )
                logger.info(f"Incremented {api_name} API usage counter.")

    def _here_reset_at(self) -> datetime | None:
        """End of the active 24h window - 24h after its first HERE call.

        None when no window is active: HERE is available immediately and the
        next call opens a fresh window.
        """
        if self._here_window_started_at is None:
            return None
        return self._here_window_started_at + HERE_WINDOW

    def _check_here_usage_limit(self) -> bool:
        """Check if we've reached the HERE API quota for the current window."""
        if date.today() != self._usage_date:
            # The date rolled over while this process was running: reseed
            # the OSM day row and re-sync the HERE window from the DB.
            self._init_api_usage_counter()
        if (
            self._here_window_started_at is not None
            and datetime.now() >= self._here_window_started_at + HERE_WINDOW
        ):
            # The 24h window expired mid-run: the quota is fresh and the
            # next HERE call opens a new window.
            self._here_window_started_at = None
            self.here_daily_requests = 0
        if self.here_daily_requests >= 950:  # Use 950 as a safety margin
            reset_at = self._here_reset_at()
            reset_note = (
                f"{reset_at:%Y-%m-%d %H:%M} (local time)" if reset_at else "now"
            )
            logger.warning(
                f"HERE API quota approaching: "
                f"{self.here_daily_requests}/1000 requests in the current "
                f"24h window. HERE re-enables at {reset_note}."
            )
            return False
        return True

    def _calculate_address_hash(self, address_standardized: str) -> str:
        """Calculate a hash for an address string."""
        # Normalize the address (remove extra spaces, convert to lowercase)
        normalized_address = " ".join(address_standardized.lower().split())
        return hashlib.md5(normalized_address.encode()).hexdigest()

    def _check_cache(self, address_id: int) -> dict[str, Any] | None:
        """Check if an address has already been geocoded based on address_id."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                SELECT address_id, address_standardized, city, corrected_address, latitude, longitude, 
                       confidence, source, status
                FROM unique_addresses 
                WHERE address_id = :address_id
            """),
                {"address_id": address_id},
            )

            row = result.fetchone()

            if row and (
                row[4] is not None and row[5] is not None
            ):  # Check if latitude and longitude are not null
                return {
                    "id": row[0],
                    "original_address": (
                        f"{row[1]}, {row[2]}" if row[2] else row[1]
                    ),  # Combine address and city
                    "corrected_address": row[3],
                    "latitude": row[4],
                    "longitude": row[5],
                    "confidence": row[6],
                    "source": row[7],
                    "status": row[8],
                    "cached": True,
                }

        return None

    def _get_full_address(self, address_id: int) -> tuple[str, str, str]:
        """Get the full address (address_standardized + city + state) from unique_addresses table."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                SELECT address_standardized, city FROM unique_addresses 
                WHERE address_id = :address_id
            """),
                {"address_id": address_id},
            )

            row = result.fetchone()

            if row:
                address_standardized = row[0]
                city = row[1] if row[1] else ""

                # Check if state is already present in the address
                has_tn = (
                    re.search(
                        r"\bTN\b|\bTennessee\b",
                        f"{address_standardized}, {city}",
                        re.IGNORECASE,
                    )
                    is not None
                )

                # Add the state if not present
                if not has_tn:
                    full_address = (
                        f"{address_standardized}, {city}, TN"
                        if city
                        else f"{address_standardized}, TN"
                    )
                else:
                    full_address = (
                        f"{address_standardized}, {city}"
                        if city
                        else address_standardized
                    )

                return address_standardized, city, full_address

            return "", "", ""

    def _throttle_osm(self) -> None:
        """Sleep just enough to respect Nominatim's 1 req/s policy."""
        elapsed: float = time.monotonic() - self._last_osm_call
        if elapsed < NOMINATIM_MIN_INTERVAL_S:
            time.sleep(NOMINATIM_MIN_INTERVAL_S - elapsed)
        self._last_osm_call = time.monotonic()

    def geocode_with_osm(self, full_address: str) -> dict[str, Any]:
        """
        Geocode an address using OpenStreetMap's Nominatim API.

        Args:
            full_address: The complete address to geocode

        Returns:
            A dictionary with geocoding results
        """
        logger.info(f"Geocoding with OSM: {full_address}")

        # Add TN to address if not present
        if not re.search(r"\bTN\b|\bTennessee\b", full_address, re.IGNORECASE):
            full_address = f"{full_address}, TN, USA"

        try:
            # Add delay to respect Nominatim usage policy
            self._throttle_osm()

            # ONLY use the 'q' parameter - no structured parameters
            params = {
                "q": full_address,
                "format": "json",
                "addressdetails": 1,
                "limit": 1,
                "accept-language": "en",
                "countrycodes": "us",  # This is not a structured parameter
            }

            headers = {"User-Agent": USER_AGENT}

            response = requests.get(
                self.osm_endpoint, params=params, headers=headers, timeout=30
            )

            if response.status_code == 200:
                results = response.json()

                if results and len(results) > 0:
                    result = results[0]

                    # Verify the result is in Tennessee
                    address = result.get("address", {})
                    state = address.get("state", "")

                    if not state or state.lower() not in ("tennessee", "tn"):
                        logger.warning(f"OSM result not in Tennessee: {full_address}")
                        return {
                            "status": "FAILED",
                            "error": "Address not in Tennessee",
                            "source": "OSM",
                        }

                    # The state check above is not enough: Nominatim resolves
                    # ambiguous street names ("MADISON", "OLD HICKORY") to
                    # other Tennessee towns. Reject anything outside the
                    # Nashville-area box so the caller falls through to HERE.
                    latitude, longitude = float(result["lat"]), float(result["lon"])
                    if not is_within_nashville_bounds(latitude, longitude):
                        logger.warning(
                            f"OSM result outside the Nashville-area bounding box "
                            f"({latitude}, {longitude}): {full_address}"
                        )
                        return {
                            "status": "FAILED",
                            "error": "Result outside the Nashville-area bounding box",
                            "source": "OSM",
                        }

                    # Calculate confidence based on various factors
                    confidence = min(float(result.get("importance", 0.5)), 1.0)

                    return {
                        "latitude": latitude,
                        "longitude": longitude,
                        "match": result.get("display_name", full_address),
                        "confidence": confidence,
                        "source": "OSM",
                        "status": "GEOCODED",
                        "raw_response": result,
                    }
                else:
                    logger.warning(
                        f"OSM geocoding returned no results for: {full_address}"
                    )
                    return {
                        "status": "FAILED",
                        "error": "No results found",
                        "source": "OSM",
                    }
            else:
                logger.error(
                    f"OSM geocoding failed with status {response.status_code}: {response.text}"
                )
                return {
                    "status": "FAILED",
                    "error": f"API error: {response.status_code}",
                    "source": "OSM",
                }

        except Exception as e:
            logger.error(f"Error geocoding with OSM: {e}")
            return {"status": "FAILED", "error": str(e), "source": "OSM"}

    def geocode_with_here(self, full_address: str) -> dict[str, Any]:
        """
        Geocode an address using HERE API.

        Args:
            full_address: The complete address to geocode

        Returns:
            A dictionary with geocoding results
        """
        # Check if we have an API key
        if not self.here_api_key:
            logger.error("HERE API key not found")
            return {
                "status": "FAILED",
                "error": "HERE API key not configured",
                "source": "HERE",
            }

        # Add TN to address if not present
        if not re.search(r"\bTN\b|\bTennessee\b", full_address, re.IGNORECASE):
            full_address = f"{full_address}, TN, USA"

        # Check usage limits. A failed check implies an active window (the
        # count can only be at the cap inside one), so reset_at is never None
        # here; the fallback keeps the payload well-formed regardless.
        if not self._check_here_usage_limit():
            logger.error("HERE API quota reached for the current 24h window")
            reset_at = self._here_reset_at()
            return {
                "status": "FAILED",
                "error": "API quota reached for the current 24h window",
                "retry_after": (reset_at or datetime.now()).isoformat(),
                "source": "HERE",
            }

        logger.info(f"Geocoding with HERE: {full_address}")

        try:
            # Add delay to respect usage policy
            time.sleep(1)
            # Remove the 'in' parameter completely and rely on address specification
            params = {"q": full_address, "apiKey": self.here_api_key}

            response: requests.Response = requests.get(
                self.here_endpoint, params=params, timeout=30
            )

            if response.status_code in (401, 403):
                # Credential problem: no retry will succeed until .env is fixed.
                # Disable HERE for the rest of this run instead of failing per-address.
                logger.error(
                    f"HERE API rejected the key ({response.status_code}); "
                    "disabling HERE fallback for this run. Fix HERE_API_KEY in .env."
                )
                self.here_api_key = None  # geocode_with_here now short-circuits
                return {
                    "status": "FAILED",
                    "error": f"HERE API key rejected ({response.status_code})",
                    "source": "HERE",
                }

            # Count only requests that actually reached the service with valid auth
            self._increment_api_usage("HERE")

            if response.status_code == 200:
                data = response.json()

                if data.get("items") and len(data["items"]) > 0:
                    item = data["items"][0]
                    position = item.get("position", {})

                    # Verify the result is in Tennessee
                    address = item.get("address", {})
                    state = address.get("state", "")

                    # Look for Tennessee in address components
                    if not state or (state.lower() != "tennessee" and state != "TN"):
                        title = item.get("title", "")
                        if not re.search(r"\bTN\b|\bTennessee\b", title, re.IGNORECASE):
                            logger.warning(
                                f"HERE result not in Tennessee: {full_address}"
                            )
                            return {
                                "status": "FAILED",
                                "error": "Address not in Tennessee",
                                "source": "HERE",
                            }

                    # Same bounding-box guard as the OSM path: never store a
                    # result outside the Nashville area (also catches a
                    # missing position, which would otherwise store NULLs
                    # with status GEOCODED).
                    latitude, longitude = position.get("lat"), position.get("lng")
                    if (
                        latitude is None
                        or longitude is None
                        or not is_within_nashville_bounds(latitude, longitude)
                    ):
                        logger.warning(
                            f"HERE result outside the Nashville-area bounding box "
                            f"({latitude}, {longitude}): {full_address}"
                        )
                        return {
                            "status": "FAILED",
                            "error": "Result outside the Nashville-area bounding box",
                            "source": "HERE",
                        }

                    # HERE provides a score for relevance (0-1)
                    confidence = item.get("scoring", {}).get("queryScore", 0.5)

                    return {
                        "latitude": latitude,
                        "longitude": longitude,
                        "match": item.get("title", full_address),
                        "confidence": confidence,
                        "source": "HERE",
                        "status": "GEOCODED",
                        "raw_response": item,
                    }
                else:
                    logger.warning(
                        f"HERE geocoding returned no results for: {full_address}"
                    )
                    return {
                        "status": "FAILED",
                        "error": "No results found",
                        "source": "HERE",
                    }
            else:
                logger.error(
                    f"HERE geocoding failed with status {response.status_code}: {response.text}"
                )
                return {
                    "status": "FAILED",
                    "error": f"API error: {response.status_code}",
                    "source": "HERE",
                }

        except Exception as e:
            logger.error(f"Error geocoding with HERE: {e}")
            return {"status": "FAILED", "error": str(e), "source": "HERE"}

    def geocode_address(self, address_id_or_string: int | str) -> dict[str, Any]:
        """
        Geocode an address using the hybrid approach.

        Args:
            address_id_or_string: Either an address_id from unique_addresses table
                              or a direct address string to geocode

        Returns:
            A dictionary with geocoding results
        """
        address_id: None | int = None
        full_address: None | str = None

        # Determine if we have an ID or a string address
        if isinstance(address_id_or_string, int):
            address_id = address_id_or_string
            # Check cache first
            cached_result = self._check_cache(address_id)
            if cached_result:
                logger.info(f"Cache hit for address_id: {address_id}")
                cached_result["from_cache"] = True
                return cached_result

            # Get the full address (address_standardized + city)
            address_standardized, city, full_address = self._get_full_address(
                address_id
            )

            if not full_address:
                logger.warning(f"Address not found for address_id: {address_id}")
                return {
                    "status": "FAILED",
                    "error": "Address not found",
                    "source": None,
                }
        else:
            # We have a direct address string
            full_address = address_id_or_string

            # Calculate hash to check if we've seen this address before
            address_hash = self._calculate_address_hash(full_address)

            # Check if this address exists in our database
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("""
                    SELECT address_id, latitude, longitude, confidence, source, status, corrected_address
                    FROM unique_addresses
                    WHERE address_hash = :hash
                """),
                    {"hash": address_hash},
                ).fetchone()

                if result and result[1] is not None and result[2] is not None:
                    # We found a geocoded address with the same hash
                    logger.info(f"Cache hit for address hash: {address_hash}")
                    return {
                        "id": result[0],
                        "latitude": result[1],
                        "longitude": result[2],
                        "confidence": result[3],
                        "source": result[4],
                        "status": result[5],
                        "match": result[6] or full_address,
                        "from_cache": True,
                    }
                elif result:
                    # We found the address but it's not geocoded yet
                    address_id = result[0]

        if not full_address:
            logger.error("No address provided for geocoding")
            return {"status": "FAILED", "error": "No address provided", "source": None}

        # Try OpenStreetMap first
        osm_result = self.geocode_with_osm(full_address)

        # If OSM geocoding was successful, return the result
        if osm_result.get("status") == "GEOCODED":
            logger.info(f"Successful geocoding with OSM: {full_address}")

            # If we have an address_id, store the result
            if address_id:
                self.store_geocoding_result(address_id, osm_result)
            else:
                # Create a new address entry
                with self.engine.begin() as conn:
                    # Extract city and state if possible
                    address_parts = full_address.split(",")
                    address_standardized = address_parts[0].strip()
                    city = address_parts[1].strip() if len(address_parts) > 1 else None

                    result = conn.execute(
                        text("""
                        INSERT INTO unique_addresses 
                        (address_standardized, city, corrected_address, latitude, longitude, 
                         confidence, source, status, geocoded_at, address_hash)
                        VALUES 
                        (:address_standardized, :city, :corrected, :lat, :lng,
                         :confidence, :source, :status, :now, :hash)
                        RETURNING address_id
                    """),
                        {
                            "address_standardized": address_standardized,
                            "city": city,
                            "corrected": osm_result.get("match"),
                            "lat": osm_result.get("latitude"),
                            "lng": osm_result.get("longitude"),
                            "confidence": osm_result.get("confidence", 0.0),
                            "source": "OSM",
                            "status": "GEOCODED",
                            "now": datetime.now(),
                            "hash": self._calculate_address_hash(full_address),
                        },
                    )

                    inserted = result.fetchone()
                    if inserted is None:
                        raise RuntimeError(
                            "Insert into unique_addresses did not return an address_id"
                        )
                    address_id = inserted[0]
                    osm_result["id"] = address_id

                    # Update geometry if we have coordinates and PostGIS is available
                    try:
                        conn.execute(
                            text("""
                            UPDATE unique_addresses
                            SET geom = ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
                            WHERE address_id = :id
                        """),
                            {
                                "longitude": osm_result.get("longitude"),
                                "latitude": osm_result.get("latitude"),
                                "id": address_id,
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Could not update geometry: {e}")

            return osm_result

        # If OSM failed, try HERE
        logger.info(f"OSM geocoding failed, trying HERE: {full_address}")
        here_result = self.geocode_with_here(full_address)

        # Store the result (whether successful or not)
        if address_id:
            self.store_geocoding_result(address_id, here_result)
        elif here_result.get("status") == "GEOCODED":
            # Create a new address entry
            with self.engine.begin() as conn:
                # Extract city and state if possible
                address_parts = full_address.split(",")
                address_standardized = address_parts[0].strip()
                city = address_parts[1].strip() if len(address_parts) > 1 else None

                result = conn.execute(
                    text("""
                    INSERT INTO unique_addresses
                    (address_standardized, city, corrected_address, latitude, longitude,
                     confidence, source, status, geocoded_at, address_hash)
                    VALUES
                    (:address_standardized, :city, :corrected, :lat, :lng,
                     :confidence, :source, :status, :now, :hash)
                    RETURNING address_id
                """),
                    {
                        "address_standardized": address_standardized,
                        "city": city,
                        "corrected": here_result.get("match"),
                        "lat": here_result.get("latitude"),
                        "lng": here_result.get("longitude"),
                        "confidence": here_result.get("confidence", 0.0),
                        "source": "HERE",
                        "status": "GEOCODED",
                        "now": datetime.now(),
                        "hash": self._calculate_address_hash(full_address),
                    },
                )

                inserted = result.fetchone()
                if inserted is None:
                    raise RuntimeError(
                        "Insert into unique_addresses did not return an address_id"
                    )
                address_id = inserted[0]
                here_result["id"] = address_id

                # Update geometry if we have coordinates and PostGIS is available
                try:
                    conn.execute(
                        text("""
                        UPDATE unique_addresses
                        SET geom = ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
                        WHERE address_id = :id
                    """),
                        {
                            "longitude": here_result.get("longitude"),
                            "latitude": here_result.get("latitude"),
                            "id": address_id,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Could not update geometry: {e}")

        return here_result

    def store_geocoding_result(self, address_id: int, result: dict[str, Any]) -> None:
        """
        Store geocoding results in the database.

        Args:
            address_id: The address_id from unique_addresses table
            result: The geocoding result dictionary
        """
        # Extract relevant data
        latitude = result.get("latitude")
        longitude = result.get("longitude")
        corrected_address = result.get("match")
        confidence = result.get("confidence", 0.0)
        source = result.get("source")
        status = result.get("status", "FAILED")

        with self.engine.begin() as conn:
            # Check if this address exists
            existing = conn.execute(
                text("""
                SELECT address_id FROM unique_addresses WHERE address_id = :address_id
            """),
                {"address_id": address_id},
            ).fetchone()

            now = datetime.now()

            if existing:
                # Update existing record
                conn.execute(
                    text("""
                    UPDATE unique_addresses
                    SET corrected_address = :corrected_address,
                        latitude = :latitude,
                        longitude = :longitude,
                        confidence = :confidence,
                        source = :source,
                        status = :status,
                        last_updated = :now,
                        geocoded_at = CASE WHEN geocoded_at IS NULL THEN :now ELSE geocoded_at END
                    WHERE address_id = :address_id
                """),
                    {
                        "corrected_address": corrected_address,
                        "latitude": latitude,
                        "longitude": longitude,
                        "confidence": confidence,
                        "source": source,
                        "status": status,
                        "now": now,
                        "address_id": address_id,
                    },
                )

                # Update geometry if we have coordinates and PostGIS is available
                if latitude and longitude:
                    try:
                        conn.execute(
                            text("""
                            UPDATE unique_addresses
                            SET geom = ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
                            WHERE address_id = :address_id
                        """),
                            {
                                "longitude": longitude,
                                "latitude": latitude,
                                "address_id": address_id,
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Could not update geometry: {e}")

                logger.info(f"Updated geocoding result for address_id: {address_id}")
            else:
                logger.warning(f"Address ID not found: {address_id}")

    def bulk_geocode_addresses(
        self, address_ids: list[int], batch_size: int = 100
    ) -> list[dict[str, Any]]:
        """
        Geocode multiple addresses in batch.

        Args:
            address_ids: List of address_ids from unique_addresses table
            batch_size: Number of addresses to process in each batch

        Returns:
            List of geocoding results
        """
        results: list[dict[str, Any]] = []
        total = len(address_ids)

        logger.info(f"Starting bulk geocoding of {total} addresses")

        for i in range(0, total, batch_size):
            batch = address_ids[i : i + batch_size]
            batch_results: list[dict[str, Any]] = []

            logger.info(
                f"Processing batch {i // batch_size + 1}/{(total - 1) // batch_size + 1} ({len(batch)} addresses)"
            )

            for address_id in batch:
                result = self.geocode_address(address_id)
                batch_results.append(result)

            results.extend(batch_results)

            # Log progress
            success_count = sum(
                1 for r in batch_results if r.get("status") == "GEOCODED"
            )
            logger.info(
                f"Batch complete: {success_count}/{len(batch)} successful ({i + len(batch)}/{total} total)"
            )

            # Check HERE usage limit
            if not self._check_here_usage_limit():
                logger.warning(
                    "HERE API quota reached for the current 24h window, "
                    "stopping batch processing"
                )
                break

        return results

    def get_recent_geocoding_results(
        self, limit: int = 100, status: None | str = None
    ) -> list[dict[str, Any]]:
        """
        Get recent geocoding results from the database.

        Args:
            limit: Maximum number of results to return
            status: Filter by status (GEOCODED, FAILED, MANUALLY_CORRECTED)

        Returns:
            List of geocoding results
        """
        query = """
            SELECT 
                address_id, address_standardized, city, corrected_address, latitude, longitude,
                confidence, source, status, geocoded_at
            FROM unique_addresses
        """

        params: dict[str, Any] = {"limit": limit}

        if status:
            query += " WHERE status = :status"
            params["status"] = status

        query += " ORDER BY geocoded_at DESC LIMIT :limit"

        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()

            results: list[dict[str, Any]] = []
            for row in rows:
                full_address = f"{row[1]}, {row[2]}" if row[2] else row[1]
                results.append(
                    {
                        "id": row[0],
                        "original_address": full_address,
                        "corrected_address": row[3],
                        "latitude": row[4],
                        "longitude": row[5],
                        "confidence": row[6],
                        "source": row[7],
                        "status": row[8],
                        "geocoded_at": row[9],
                    }
                )

            return results

    def get_api_usage_stats(self) -> dict[str, Any]:
        """
        Get API usage statistics.

        Returns:
            Dictionary with API usage statistics
        """
        today = date.today()

        with self.engine.connect() as conn:
            # Get today's usage
            today_result = conn.execute(
                text("""
                SELECT api_name, request_count
                FROM api_usage
                WHERE request_date = :today
            """),
                {"today": today},
            ).fetchall()

            today_usage = {row[0]: row[1] for row in today_result}

            # Get historical usage
            historical_result = conn.execute(
                text("""
                SELECT api_name, request_date, request_count
                FROM api_usage
                WHERE request_date < :today
                ORDER BY request_date DESC
                LIMIT 30
            """),
                {"today": today},
            ).fetchall()

            historical_usage = {}
            for row in historical_result:
                api_name, request_date, count = row
                if api_name not in historical_usage:
                    historical_usage[api_name] = []
                historical_usage[api_name].append(
                    {"date": request_date, "count": count}
                )

            # HERE quota numbers come from the rolling 24h window, which can
            # span two calendar dates - today's row alone would undercount.
            window_row = conn.execute(text("""
                SELECT request_count, window_started_at FROM api_usage
                WHERE api_name = 'HERE' AND window_started_at IS NOT NULL
                ORDER BY window_started_at DESC
                LIMIT 1
            """)).fetchone()

        window_used = 0
        window_resets_at: str | None = None
        if window_row is not None:
            window_start: datetime = window_row[1]
            if datetime.now() < window_start + HERE_WINDOW:
                window_used = int(window_row[0])
                window_resets_at = (window_start + HERE_WINDOW).isoformat(
                    sep=" ", timespec="minutes"
                )

        return {
            "today_usage": today_usage,
            "historical_usage": historical_usage,
            "here_daily_limit": 1000,
            "here_window_used": window_used,
            "here_daily_remaining": 1000 - window_used,
            # None = no active window; the next HERE call opens one.
            "here_resets_at": window_resets_at,
        }

    def manually_update_address(
        self,
        address_id: int,
        corrected_address: str,
        latitude: float | None,
        longitude: float | None,
        changed_by: str,
        reason: str,
    ) -> bool:
        """
        Manually update an address's geocoding information.

        Args:
            address_id: ID of the address to update
            corrected_address: New corrected address
            latitude: New latitude
            longitude: New longitude
            changed_by: Name of the person making the change
            reason: Reason for the change

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.engine.begin() as conn:
                # Get current values for logging changes
                current = conn.execute(
                    text("""
                    SELECT address_standardized, city, corrected_address, latitude, longitude
                    FROM unique_addresses
                    WHERE address_id = :id
                """),
                    {"id": address_id},
                ).fetchone()

                if not current:
                    logger.error(f"Address ID {address_id} not found")
                    return False

                now = datetime.now()

                # Update the address
                conn.execute(
                    text("""
                    UPDATE unique_addresses
                    SET corrected_address = :corrected_address,
                        latitude = :latitude,
                        longitude = :longitude,
                        status = 'MANUALLY_CORRECTED',
                        last_updated = :now
                    WHERE address_id = :id
                """),
                    {
                        "corrected_address": corrected_address,
                        "latitude": latitude,
                        "longitude": longitude,
                        "now": now,
                        "id": address_id,
                    },
                )

                # Update geometry if PostGIS is available
                if latitude is not None and longitude is not None:
                    try:
                        conn.execute(
                            text("""
                            UPDATE unique_addresses
                            SET geom = ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
                            WHERE address_id = :id
                        """),
                            {
                                "longitude": longitude,
                                "latitude": latitude,
                                "id": address_id,
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Could not update geometry: {e}")

                # Log the changes
                if current[2] != corrected_address:
                    conn.execute(
                        text("""
                        INSERT INTO address_correction_log
                        (address_id, original_value, new_value, field_changed, changed_by, changed_at, reason)
                        VALUES
                        (:address_id, :original_value, :new_value, :field_changed, :changed_by, :now, :reason)
                    """),
                        {
                            "address_id": address_id,
                            "original_value": current[2],
                            "new_value": corrected_address,
                            "field_changed": "corrected_address",
                            "changed_by": changed_by,
                            "now": now,
                            "reason": reason,
                        },
                    )

                if (
                    (current[3] != latitude or current[4] != longitude)
                    and latitude is not None
                    and longitude is not None
                ):
                    old_coords = f"({current[3]}, {current[4]})"
                    new_coords = f"({latitude}, {longitude})"

                    conn.execute(
                        text("""
                        INSERT INTO address_correction_log
                        (address_id, original_value, new_value, field_changed, changed_by, changed_at, reason)
                        VALUES
                        (:address_id, :original_value, :new_value, :field_changed, :changed_by, :now, :reason)
                    """),
                        {
                            "address_id": address_id,
                            "original_value": old_coords,
                            "new_value": new_coords,
                            "field_changed": "coordinates",
                            "changed_by": changed_by,
                            "now": now,
                            "reason": reason,
                        },
                    )

                logger.info(f"Manually updated address ID {address_id} by {changed_by}")
                return True

        except Exception as e:
            logger.error(f"Error manually updating address_standardized: {e}")
            return False


if __name__ == "__main__":
    # Example usage
    service = GeocodingService()

    # Get all address IDs that need geocoding
    with service.engine.connect() as conn:
        results = conn.execute(text("""
            SELECT address_id FROM unique_addresses
            WHERE (latitude IS NULL OR longitude IS NULL)
            LIMIT 5
        """)).fetchall()

        address_ids = [row[0] for row in results]

    # Geocode them
    for address_id in address_ids:
        result = service.geocode_address(address_id)
        print(f"Address ID {address_id}: {result.get('status')}")

        if result.get("status") == "GEOCODED":
            print(
                f"  Coordinates: ({result.get('latitude')}, {result.get('longitude')})"
            )
            print(f"  Source: {result.get('source')}")
        else:
            print(f"  Error: {result.get('error')}")

    # Get API usage stats
    stats = service.get_api_usage_stats()
    print("\nAPI Usage Stats:")
    print(f"  TODAY - HERE: {stats['today_usage'].get('HERE', 0)}/1000")
    print(f"  TODAY - OSM: {stats['today_usage'].get('OSM', 0)}")
