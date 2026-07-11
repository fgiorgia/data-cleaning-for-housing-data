import argparse
import os
import shutil
import subprocess
from pathlib import Path

from scripts.config import get_db_config

# On Windows the client tools (psql, pg_restore) are frequently not on PATH;
# fall back to the newest versioned install under this directory.
WINDOWS_PG_BASE = r"C:\Program Files\PostgreSQL"

# Admin commands (existence check, CREATE/DROP DATABASE) must run against a
# database that is guaranteed to exist — this is the connection psql uses to
# run CREATE DATABASE, not the database being created. Only "postgres" is
# guaranteed on a fresh server; the restore target (default geocoded_housing)
# is created by this script itself.
MAINTENANCE_DB = "postgres"


def find_pg_tool(name: str) -> str:
    on_path = shutil.which(name)
    if on_path:
        return on_path

    base = Path(WINDOWS_PG_BASE)
    if base.is_dir():
        exe = name + (".exe" if os.name == "nt" else "")
        for version_dir in sorted(base.iterdir(), key=lambda p: p.name, reverse=True):
            candidate = version_dir / "bin" / exe
            if candidate.is_file():
                return str(candidate)

    raise FileNotFoundError(
        f"Could not find '{name}'. Add the PostgreSQL bin directory to your PATH "
        f"(e.g. {WINDOWS_PG_BASE}\\18\\bin)."
    )


def run(
    cmd: list[str], env: dict[str, str], capture: bool = False
) -> subprocess.CompletedProcess[str]:
    # Credentials travel through PGPASSWORD in the environment, never argv, so
    # echoing the command is safe.
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, env=env, capture_output=capture, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore data/migration_dump.backup into a dedicated database."
    )
    parser.add_argument("--backup", default="./data/migration_dump.backup")
    parser.add_argument("--dbname", default="geocoded_housing")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop the target database first if it already exists.",
    )
    args = parser.parse_args()

    cfg = get_db_config()
    if cfg["password"] is None:
        print("Error: Missing Postgres password, ensure your env is set-up correctly")
        return exit(1)

    backup_path = Path(args.backup)
    if not backup_path.is_file():
        print(f"Error: backup file not found: {backup_path}")
        return exit(1)

    # Guard: never drop or overwrite the configured database or the
    # maintenance database the admin commands run against.
    if args.dbname in (cfg["database"], MAINTENANCE_DB):
        print(
            f"Error: refusing to target '{args.dbname}'. "
            "Choose a dedicated --dbname (default: geocoded_housing)."
        )
        return exit(1)

    env = os.environ.copy()
    env["PGPASSWORD"] = cfg["password"]

    psql = find_pg_tool("psql")
    pg_restore = find_pg_tool("pg_restore")

    admin = [
        psql,
        "--host",
        cfg["hostname"],
        "--port",
        cfg["port"],
        "--username",
        cfg["username"],
        "--dbname",
        MAINTENANCE_DB,
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
    ]

    exists = (
        run(
            admin
            + ["-tAc", f"SELECT 1 FROM pg_database WHERE datname = '{args.dbname}'"],
            env=env,
            capture=True,
        ).stdout.strip()
        == "1"
    )

    if exists and not args.recreate:
        print(
            f"Database '{args.dbname}' already exists. "
            "Re-run with --recreate to drop and rebuild it."
        )
        return exit(1)

    if exists and args.recreate:
        run(
            admin + ["-c", f'DROP DATABASE IF EXISTS "{args.dbname}" WITH (FORCE)'],
            env=env,
        )

    run(admin + ["-c", f'CREATE DATABASE "{args.dbname}"'], env=env)

    # PostGIS repopulates spatial_ref_sys when the extension is created, so the
    # dump's own copy of that table would raise a duplicate-key conflict. Drop
    # just that data entry from the restore list; everything else loads cleanly.
    toc = run([pg_restore, "-l", str(backup_path)], env=env, capture=True).stdout
    filtered = [
        ln for ln in toc.splitlines() if "TABLE DATA public spatial_ref_sys" not in ln
    ]
    toc_path = Path("./out/restore_toc.list")
    toc_path.parent.mkdir(parents=True, exist_ok=True)
    toc_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")

    run(
        [
            pg_restore,
            "--host",
            cfg["hostname"],
            "--port",
            cfg["port"],
            "--username",
            cfg["username"],
            "--dbname",
            args.dbname,
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "-L",
            str(toc_path),
            str(backup_path),
        ],
        env=env,
    )

    print(f"Restore complete -> database '{args.dbname}'")


if __name__ == "__main__":
    main()
