"""Run psql against the configured database, forwarding all CLI arguments.

The password travels through PGPASSWORD in the environment, never argv,
so it cannot leak into shell history or process listings.
"""

from __future__ import annotations

import os
import subprocess
import sys

from scripts.config import DBConfig, get_db_config


def main() -> None:
    db_config: DBConfig = get_db_config()
    password: str | None = db_config["password"]
    if password is None:
        print("Error: Missing Postgres password, ensure your env is set-up correctly")
        sys.exit(1)

    # Build the argument list directly (no string splitting), so values
    # containing spaces or shell metacharacters can never break the command.
    command: list[str] = [
        "psql",
        "-P", "pager=off",
        f"--host={db_config['hostname']}",
        f"--username={db_config['username']}",
        f"--dbname={db_config['database']}",
        f"--port={db_config['port']}",
        *sys.argv[1:],
    ]
    custom_env: dict[str, str] = os.environ.copy()
    custom_env["PGPASSWORD"] = password

    subprocess.run(command, check=True, env=custom_env)


if __name__ == "__main__":
    main()