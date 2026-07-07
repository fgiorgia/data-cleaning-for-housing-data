"""Unit tests for scripts/config.py."""

from __future__ import annotations

import pytest
from scripts.config import DBConfig, get_db_config

ENV_VARS: tuple[str, ...] = (
    "DB_HOSTNAME",
    "DB_PORT",
    "DB_DATABASE",
    "DB_USERNAME",
    "DB_PASSWORD",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_env_is_empty(clean_env: None) -> None:
    config: DBConfig = get_db_config()
    assert config == {
        "hostname": "localhost",
        "port": "5432",
        "database": "postgres",
        "username": "postgres",
        "password": None,
    }


def test_environment_overrides(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_HOSTNAME", "db.internal")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_DATABASE", "housing")
    monkeypatch.setenv("DB_USERNAME", "analyst")
    monkeypatch.setenv("DB_PASSWORD", "s3cret")

    config: DBConfig = get_db_config()
    assert config["hostname"] == "db.internal"
    assert config["port"] == "5433"
    assert config["database"] == "housing"
    assert config["username"] == "analyst"
    assert config["password"] == "s3cret"
