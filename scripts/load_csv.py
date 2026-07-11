"""Load a CSV file into PostgreSQL in chunks.

The target table is REPLACED on each run (first chunk uses
if_exists='replace', later chunks append), so re-running the pipeline can
never duplicate the raw data.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator

import pandas as pd
from sqlalchemy import URL, Engine, create_engine

from scripts.config import DBConfig, get_db_config


def build_engine(db_config: DBConfig) -> Engine:
    # URL.create escapes special characters (@, /, %, ...) in the password
    # and keeps it out of any string formatting. psycopg (v3) is the
    # actively developed PostgreSQL driver; psycopg2 is in maintenance mode.
    url: URL = URL.create(
        drivername="postgresql+psycopg",
        username=db_config["username"],
        password=db_config["password"],
        host=db_config["hostname"],
        port=int(db_config["port"]),
        database=db_config["database"],
    )
    return create_engine(url)


def load_csv_to_db(dataset_file: str, table: str, db_config: DBConfig) -> int:
    engine: Engine = build_engine(db_config)
    total_rows: int = 0
    try:
        chunks: Iterator[pd.DataFrame] = pd.read_csv(
            dataset_file, chunksize=1000, encoding="utf-8"
        )
        for index, df in enumerate(chunks):
            df.to_sql(
                table,
                engine,
                index=False,
                # 'replace' on the first chunk drops any leftover table from
                # a previous run; subsequent chunks append to the fresh one.
                if_exists="replace" if index == 0 else "append",
            )
            total_rows += len(df)
            print(f"... {total_rows} rows")
    finally:
        engine.dispose()
    return total_rows


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Load a CSV file into a PostgreSQL table (replacing it)."
    )
    # Flag names are kept as-is for compatibility with the poe tasks.
    parser.add_argument("--datasetFile", dest="dataset_file", required=True)
    parser.add_argument("--table", default="dataset")
    args: argparse.Namespace = parser.parse_args()

    db_config: DBConfig = get_db_config()
    if db_config["password"] is None:
        print("Error: Missing Postgres password, ensure your env is set-up correctly")
        sys.exit(1)

    print("Copying dataset to database...")
    total_rows: int = load_csv_to_db(args.dataset_file, args.table, db_config)
    print(f"Complete! Loaded {total_rows} rows into '{args.table}'.")


if __name__ == "__main__":
    main()
