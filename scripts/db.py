"""Single place to build SQLAlchemy engines for this project.

Why this module exists:

- `URL.create` escapes special characters (``@``, ``/``, ``%``, ...) in the
  password. Three modules used to interpolate credentials into an f-string
  URL, which breaks on such passwords and leaks two different dialect
  defaults into the codebase.
- The dialect is pinned to ``postgresql+psycopg`` (psycopg v3, actively
  developed) so the project needs exactly one PostgreSQL driver.

Every module that talks to the database should do::

    from scripts.db import get_engine
    engine = get_engine()
"""

from __future__ import annotations

from sqlalchemy import URL, Engine, create_engine

from scripts.config import DBConfig, get_db_config

DRIVERNAME: str = "postgresql+psycopg"


def build_url(db_config: DBConfig) -> URL:
    """Build a safely-escaped SQLAlchemy URL from a config mapping."""
    return URL.create(
        drivername=DRIVERNAME,
        username=db_config["username"],
        password=db_config["password"],
        host=db_config["hostname"],
        port=int(db_config["port"]),
        database=db_config["database"],
    )


def get_engine(db_config: DBConfig | None = None) -> Engine:
    """Create an engine from the given config, or from the environment.

    Raises:
        ValueError: if no database password is configured, so callers fail
            with one clear message instead of an opaque auth error later.
    """
    config: DBConfig = db_config if db_config is not None else get_db_config()
    if config["password"] is None:
        raise ValueError(
            "Missing Postgres password: set DB_PASSWORD in .env or the environment."
        )
    return create_engine(build_url(config))
