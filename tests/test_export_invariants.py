"""Post-pipeline invariants on out/dataset.csv.

This replaces the inline heredoc that lived in the CI workflow: same
checks, but versioned, typed, and runnable locally with
``uv run poe test-export`` after a pipeline run.

Marked ``export`` so the default unit-test run skips it (the file only
exists after the pipeline has run).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

EXPORT_PATH: Path = Path("out/dataset.csv")

pytestmark = pytest.mark.export


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    if not EXPORT_PATH.is_file():
        pytest.fail(
            f"{EXPORT_PATH} not found - run `uv run poe data-cleaning-pipeline` first."
        )
    with EXPORT_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_export_is_not_empty(rows: list[dict[str, str]]) -> None:
    assert rows, "export is empty"


def test_no_null_property_address(rows: list[dict[str, str]]) -> None:
    missing: int = sum(1 for r in rows if not r["property_address"])
    assert missing == 0, f"{missing} row(s) with NULL property_address"


def test_sold_as_vacant_is_boolean(rows: list[dict[str, str]]) -> None:
    bad: set[str] = {r["sold_as_vacant"] for r in rows} - {"t", "f", ""}
    assert not bad, f"non-boolean sold_as_vacant values: {sorted(bad)}"


def test_no_double_spaces_remain(rows: list[dict[str, str]]) -> None:
    offenders: int = sum(1 for r in rows for v in r.values() if "  " in v)
    assert offenders == 0, f"{offenders} value(s) still contain double spaces"


def test_sale_price_parses_as_number(rows: list[dict[str, str]]) -> None:
    for r in rows[:1000]:  # sample; full column already validated in SQL
        value: str = r["sale_price"]
        if value:
            float(value)  # raises ValueError on regression
