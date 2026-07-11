"""Back up the durable geocode cache.

Dumps ``unique_addresses`` and ``address_correction_log`` from the
configured database (``geocoded_housing``) to a gitignored local file. This
is the disaster-recovery artifact for geocoding work now that
``data/migration_dump.backup`` is no longer the distribution mechanism:
geocoding is manual and rate-limited, so the cache it fills is the only
copy of that work.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from scripts.config import get_db_config
from scripts.pg_tools import find_pg_tool, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="./out/geocodes.backup")
    args = parser.parse_args()

    cfg = get_db_config()
    if cfg["password"] is None:
        print("Error: Missing Postgres password, ensure your env is set up correctly.")
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PGPASSWORD"] = cfg["password"]

    pg_dump = find_pg_tool("pg_dump")

    run(
        [
            pg_dump,
            "--host",
            cfg["hostname"],
            "--port",
            cfg["port"],
            "--username",
            cfg["username"],
            "--dbname",
            cfg["database"],
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--table=unique_addresses",
            "--table=address_correction_log",
            f"--file={output_path}",
        ],
        env=env,
    )

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Backup written: {output_path} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
