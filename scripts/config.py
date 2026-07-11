"""Database configuration, read once from the environment / ``.env``.

The password is ``None`` when unset so callers can fail fast with a clear
message; every other field has a sensible local-development default.
"""

from __future__ import annotations

import os
from typing import TypedDict

from dotenv import load_dotenv

load_dotenv()


class DBConfig(TypedDict):
    hostname: str
    port: str
    database: str
    username: str
    password: str | None


def get_db_config() -> DBConfig:
    return {
        "hostname": os.environ.get("DB_HOSTNAME", "localhost"),
        "port": os.environ.get("DB_PORT", "5432"),
        "database": os.environ.get("DB_DATABASE", "postgres"),
        "username": os.environ.get("DB_USERNAME", "postgres"),
        "password": os.environ.get("DB_PASSWORD"),
    }
