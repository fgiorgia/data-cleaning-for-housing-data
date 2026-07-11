"""Unit tests for scripts/db.py — the exact bug class it prevents is a
password containing URL metacharacters breaking an f-string connection URL.
"""

from __future__ import annotations

import pytest
from scripts.config import DBConfig
from scripts.db import build_url, get_engine
from sqlalchemy import URL, Engine


def make_config(password: str | None) -> DBConfig:
    return {
        "hostname": "localhost",
        "port": "5432",
        "database": "housing",
        "username": "postgres",
        "password": password,
    }


def test_password_with_url_metacharacters_survives() -> None:
    nasty: str = "p@ss/word%:#?&"
    url: URL = build_url(make_config(nasty))
    # URL.create stores the raw password; rendering escapes it.
    assert url.password == nasty
    rendered: str = url.render_as_string(hide_password=False)
    assert nasty not in rendered  # it must be percent-encoded, not verbatim


def test_dialect_is_psycopg_v3() -> None:
    url: URL = build_url(make_config("pw"))
    assert url.drivername == "postgresql+psycopg"


def test_get_engine_fails_fast_without_password() -> None:
    with pytest.raises(ValueError, match="DB_PASSWORD"):
        get_engine(make_config(None))


def test_get_engine_builds_engine() -> None:
    engine: Engine = get_engine(make_config("pw"))
    try:
        assert engine.url.host == "localhost"
        assert engine.url.database == "housing"
    finally:
        engine.dispose()
