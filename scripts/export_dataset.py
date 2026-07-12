"""Export the geocoded dataset to CSV.

Writes two artifacts from a single query pass:

- ``out/dataset.csv`` -- full export, including owner-address coordinates
  (the maintainer needs them). LOCAL ONLY: ``out/`` is gitignored and this
  file is never distributed.
- ``out/dataset_public.csv`` -- identical, except the owner coordinate and
  owner geocode-metadata columns are blanked on every row before writing,
  so no geocoded mailing address ever ships. See "Why owner addresses are
  redacted" below.

The cleaning pipeline no longer exports ``housing_data`` directly
(``src/cleaning.sql`` used to \\copy it out); this script is now the only
exporter, and it joins in geocode coordinates that the raw pipeline output
never had.

Why owner addresses are redacted
---------------------------------
Property-sale records are public in Tennessee, but a repo that pairs owner
names with geocoded mailing addresses lowers the lookup barrier in a way
the county's own systems don't. Redacting owner-only geocodes is the
minimal intervention: property addresses (the subject of the sale) keep
their coordinates; owner addresses (where the seller receives mail) lose
theirs.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.engine import CursorResult

from scripts.db import get_engine

QUERY = """
SELECT
    hd.unique_id, hd.parcel_id, hd.land_use, hd.property_address, hd.sale_date,
    hd.sale_price, hd.legal_reference, hd.sold_as_vacant, hd.owner_name,
    hd.owner_address, hd.acreage, hd.tax_district, hd.land_value,
    hd.building_value, hd.total_value, hd.year_built, hd.bedrooms,
    hd.full_bath, hd.half_bath, hd.currency_code, hd.property_address_imputed,
    hd.owner_address_imputed,
    pua.latitude AS property_latitude,
    pua.longitude AS property_longitude,
    pua.geocode_source AS property_geocode_source,
    pua.geocode_confidence AS property_geocode_confidence,
    oua.latitude AS owner_latitude,
    oua.longitude AS owner_longitude,
    oua.geocode_source AS owner_geocode_source,
    oua.geocode_confidence AS owner_geocode_confidence
FROM housing_data hd
LEFT JOIN address_mappings pam
    ON pam.housing_id = hd.unique_id AND pam.address_type = 'property'
LEFT JOIN unique_addresses pua ON pua.address_id = pam.address_id
LEFT JOIN address_mappings oam
    ON oam.housing_id = hd.unique_id AND oam.address_type = 'owner'
LEFT JOIN unique_addresses oua ON oua.address_id = oam.address_id
ORDER BY hd.unique_id;
"""

OWNER_COLUMNS: tuple[str, ...] = (
    "owner_latitude",
    "owner_longitude",
    "owner_geocode_source",
    "owner_geocode_confidence",
)

FULL_PATH = Path("out/dataset.csv")
PUBLIC_PATH = Path("out/dataset_public.csv")


def csv_value(value: object) -> str:
    """Render a DB value the way psql's \\copy CSV format would."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "t" if value else "f"
    return str(value)


def export(engine: Engine, full_path: Path, public_path: Path) -> int:
    """Run the query and write both CSVs. Returns the row count."""
    full_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        engine.connect() as conn,
        full_path.open("w", encoding="utf-8", newline="") as full_f,
        public_path.open("w", encoding="utf-8", newline="") as public_f,
    ):
        result: CursorResult[tuple[object, ...]] = conn.execute(text(QUERY))
        fieldnames: list[str] = list(result.keys())

        full_writer = csv.DictWriter(full_f, fieldnames=fieldnames)
        public_writer = csv.DictWriter(public_f, fieldnames=fieldnames)
        full_writer.writeheader()
        public_writer.writeheader()

        row_count = 0
        for row in result.mappings():
            record: dict[str, str] = {k: csv_value(v) for k, v in row.items()}
            full_writer.writerow(record)
            public_record: dict[str, str] = dict(record)
            for column in OWNER_COLUMNS:
                public_record[column] = ""
            public_writer.writerow(public_record)
            row_count += 1

    return row_count


def main() -> None:
    row_count: int = export(get_engine(), FULL_PATH, PUBLIC_PATH)
    print(f"Exported {row_count} rows to {FULL_PATH} and {PUBLIC_PATH}")


if __name__ == "__main__":
    main()
