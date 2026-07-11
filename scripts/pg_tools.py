import os
import shutil
import subprocess
from pathlib import Path

# On Windows the client tools (psql, pg_dump, pg_restore) are frequently not
# on PATH; fall back to the newest versioned install under this directory.
WINDOWS_PG_BASE = r"C:\Program Files\PostgreSQL"


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
