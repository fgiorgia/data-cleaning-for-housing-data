"""Produce a privacy-sanitised pg_dump of the housing database.

This is a **maintainer** tool, not an end-user tool. Run it once after
geocoding is complete (or corrected) to regenerate the distributable
``data/migration_dump.backup``. The dump is then committed (via Git LFS)
so that consumers can ``restore-backup`` without needing API keys or
sending any addresses to third-party geocoders.

What it does
------------
1. Creates a throwaway copy of the ``housing`` database.
2. Redacts geocoding results for addresses that are *only* used as owner
   mailing addresses — not as property addresses. Public property
   locations stay intact; private owner-home locations do not ship.
3. Exports the sanitised copy with ``pg_dump -Fc``.
4. Drops the throwaway database.

Why owner addresses are redacted
--------------------------------
Property-sale records are public in Tennessee, but a repo that pairs
owner names with geocoded mailing addresses lowers the lookup barrier in a
way the county's own systems don't. Redacting owner-only geocodes is the
minimal intervention: property addresses (the subject of the sale) keep
their coordinates; owner addresses (where the seller receives mail) lose
theirs. Self-hosted geocoders (Nominatim, Photon, Pelias) are an
alternative that keeps everything local.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.config import get_db_config
from scripts.restore_backup import find_pg_tool, run


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=(
            "Create a privacy-sanitised pg_dump of the housing database for public distribution."
        ),
    )
    parser.add_argument(
        "--source-db",
        default="geocoded_housing",
        help="Source database to sanitise (default: geocoded_housing).",
    )
    parser.add_argument(
        "--output",
        default="./data/migration_dump.backup",
        help="Output path for the sanitised backup (default: ./data/migration_dump.backup).",
    )
    parser.add_argument(
        "--keep-temp-db",
        action="store_true",
        help="Don't drop the temporary database after export (for inspection).",
    )
    args: argparse.Namespace = parser.parse_args()

    cfg = get_db_config()
    password: str | None = cfg["password"]
    if password is None:
        print("Error: Missing Postgres password, ensure your env is set up correctly.")
        sys.exit(1)

    temp_db: str = f"{args.source_db}_sanitised_tmp"
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    psql: str = find_pg_tool("psql")
    pg_dump: str = find_pg_tool("pg_dump")

    import os

    env: dict[str, str] = os.environ.copy()
    env["PGPASSWORD"] = password

    conn_flags: list[str] = [
        f"--host={cfg['hostname']}",
        f"--port={cfg['port']}",
        f"--username={cfg['username']}",
    ]

    # ------------------------------------------------------------------ #
    # 1. Create a throwaway copy of the source database.                 #
    # ------------------------------------------------------------------ #
    print(f"\n1/4  Creating temporary database '{temp_db}' from '{args.source_db}'...")

    # Drop any leftover temp DB from a previous interrupted run.
    run(
        [
            psql,
            *conn_flags,
            "--dbname=postgres",
            "-c",
            f'DROP DATABASE IF EXISTS "{temp_db}" WITH (FORCE);',
        ],
        env=env,
    )
    run(
        [
            psql,
            *conn_flags,
            "--dbname=postgres",
            "-c",
            f'CREATE DATABASE "{temp_db}" TEMPLATE "{args.source_db}";',
        ],
        env=env,
    )

    # ------------------------------------------------------------------ #
    # 2. Redact owner-only geocodes in the temporary copy.               #
    # ------------------------------------------------------------------ #
    print("\n2/4  Redacting owner-only geocoding results...")

    sanitise_sql: str = """
    -- Null out coordinates and geocode metadata for addresses that are ONLY
    -- linked as owner mailing addresses (never as a property address).
    -- Property-sale locations stay intact.
    UPDATE unique_addresses ua
    SET latitude          = NULL,
        longitude         = NULL,
        geom              = NULL,
        confidence        = NULL,
        source            = NULL,
        corrected_address = NULL,
        status            = 'REDACTED',
        geocoded_at       = NULL,
        last_updated      = NOW()
    WHERE EXISTS (
            SELECT 1 FROM address_mappings am
            WHERE am.address_id = ua.address_id
              AND am.address_type = 'owner')
      AND NOT EXISTS (
            SELECT 1 FROM address_mappings am
            WHERE am.address_id = ua.address_id
              AND am.address_type = 'property');

    -- Clear the correction log for redacted addresses so individual
    -- geocoding attempts (which may contain address fragments in
    -- original_value / new_value) are not redistributed either.
    DELETE FROM address_correction_log
    WHERE address_id IN (
        SELECT address_id FROM unique_addresses WHERE status = 'REDACTED'
    );
    """

    run(
        [psql, *conn_flags, f"--dbname={temp_db}", "-c", sanitise_sql],
        env=env,
    )

    # Quick sanity check: how many addresses were redacted?
    result = run(
        [
            psql,
            *conn_flags,
            f"--dbname={temp_db}",
            "-t",
            "-A",
            "-c",
            "SELECT count(*) FROM unique_addresses WHERE status = 'REDACTED';",
        ],
        env=env,
        capture=True,
    )
    redacted_count: str = result.stdout.strip()
    print(f"   Redacted {redacted_count} owner-only address(es).")

    # ------------------------------------------------------------------ #
    # 3. Export the sanitised database.                                   #
    # ------------------------------------------------------------------ #
    print(f"\n3/4  Exporting sanitised dump to {output_path}...")

    run(
        [
            pg_dump,
            *conn_flags,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={output_path}",
            temp_db,
        ],
        env=env,
    )

    size_mb: float = output_path.stat().st_size / (1024 * 1024)
    print(f"   Dump written: {output_path} ({size_mb:.1f} MB)")

    # ------------------------------------------------------------------ #
    # 4. Clean up the temporary database.                                #
    # ------------------------------------------------------------------ #
    if args.keep_temp_db:
        print(f"\n4/4  Keeping temporary database '{temp_db}' (--keep-temp-db).")
    else:
        print(f"\n4/4  Dropping temporary database '{temp_db}'...")
        run(
            [
                psql,
                *conn_flags,
                "--dbname=postgres",
                "-c",
                f'DROP DATABASE "{temp_db}" WITH (FORCE);',
            ],
            env=env,
        )

    # ------------------------------------------------------------------ #
    # Done.                                                              #
    # ------------------------------------------------------------------ #
    print("\nDone. Commit the updated dump with:")
    print(f"  git add {output_path}")
    print("  git commit -m 'Refresh sanitised backup'")


if __name__ == "__main__":
    main()
