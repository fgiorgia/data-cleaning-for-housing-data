"""Create the target database if it does not exist (idempotent).

Used as the ``ensure-clean-db`` step of the cleaning pipeline so that the
dedicated ``housing_clean`` database never has to be created by hand
(previously a manual ``CREATE DATABASE`` step in the RUNBOOK).

The target comes from ``DB_DATABASE`` (set per-task in ``pyproject.toml``)
or ``--dbname``. The check-and-create runs against the server's
``postgres`` maintenance database, so this works even when the target does
not exist yet.

Implementation note: this deliberately uses psycopg rather than shelling
out to psql. ``psql -c`` does *not* perform variable interpolation
(``:'var'`` is sent literally to the server and fails with a syntax
error), whereas psycopg gives real parameter binding for the existence
check and ``sql.Identifier`` for safely quoting the database name in
``CREATE DATABASE`` — which cannot be parameterised because it is an
identifier, not a literal.
"""

from __future__ import annotations

import argparse
import re
import sys

import psycopg
from psycopg import sql

from scripts.config import DBConfig, get_db_config

# The pre-existing database the check-and-create connects to — not the
# database being created (that comes from DB_DATABASE / --dbname).
MAINTENANCE_DB: str = "postgres"

# Conservative identifier rule: letters, digits, underscores, leading
# letter/underscore. Anything else is rejected outright — defence in depth
# on top of sql.Identifier's quoting.
VALID_DBNAME: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ensure_database(dbname: str, cfg: DBConfig) -> bool:
    """Create ``dbname`` if missing. Returns True if it was created."""
    if not VALID_DBNAME.match(dbname):
        raise ValueError(
            f"Invalid database name {dbname!r}: only letters, digits and "
            "underscores are allowed."
        )

    # CREATE DATABASE cannot run inside a transaction block, hence
    # autocommit. The connection targets the maintenance database because
    # the target database may not exist yet.
    with psycopg.connect(
        host=cfg["hostname"],
        port=int(cfg["port"]),
        user=cfg["username"],
        password=cfg["password"],
        dbname=MAINTENANCE_DB,
        autocommit=True,
    ) as conn:
        exists: bool = (
            conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
            ).fetchone()
            is not None
        )
        if exists:
            print(f"Database '{dbname}' already exists - nothing to do.")
            return False

        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        print(f"Created database '{dbname}'.")
        return True


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Create the configured database if it does not exist."
    )
    parser.add_argument(
        "--dbname",
        default=None,
        help="Database to ensure (default: DB_DATABASE from the environment).",
    )
    args: argparse.Namespace = parser.parse_args()

    cfg: DBConfig = get_db_config()
    if cfg["password"] is None:
        print("Error: Missing Postgres password, ensure your env is set up correctly.")
        sys.exit(1)

    dbname: str = args.dbname if args.dbname is not None else cfg["database"]
    try:
        ensure_database(dbname, cfg)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    except psycopg.OperationalError as exc:
        print(
            f"Error: could not reach PostgreSQL at {cfg['hostname']}:{cfg['port']} - {exc}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
