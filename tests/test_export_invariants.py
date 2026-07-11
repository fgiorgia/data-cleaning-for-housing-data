"""Post-export invariants on out/dataset.csv and out/dataset_public.csv.

This replaces the inline heredoc that lived in the CI workflow: same
checks, but versioned, typed, and runnable locally with
``uv run poe test -m export`` after an export run.

Marked ``export`` so the default unit-test run skips it (the files only
exist after ``export-dataset`` has run).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

EXPORT_PATH: Path = Path("out/dataset.csv")
PUBLIC_EXPORT_PATH: Path = Path("out/dataset_public.csv")

# Nashville-area bounding box with generous margins.
MIN_LATITUDE = 35.0
MAX_LATITUDE = 36.7
MIN_LONGITUDE = -87.6
MAX_LONGITUDE = -85.7

OWNER_COORDINATE_COLUMNS: tuple[str, ...] = (
    "owner_latitude",
    "owner_longitude",
    "owner_geocode_source",
    "owner_geocode_confidence",
)

pytestmark = pytest.mark.export


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        pytest.fail(f"{path} not found - run `uv run poe export-dataset` first.")
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _read_rows(EXPORT_PATH)


@pytest.fixture(scope="module")
def public_rows() -> list[dict[str, str]]:
    return _read_rows(PUBLIC_EXPORT_PATH)


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


def test_coordinates_are_within_nashville_area(rows: list[dict[str, str]]) -> None:
    bad: list[str] = []
    for prefix in ("property", "owner"):
        lat_col, lon_col = f"{prefix}_latitude", f"{prefix}_longitude"
        for r in rows:
            lat_str, lon_str = r[lat_col], r[lon_col]
            if not lat_str or not lon_str:
                continue
            lat, lon = float(lat_str), float(lon_str)
            if not (MIN_LATITUDE <= lat <= MAX_LATITUDE) or not (
                MIN_LONGITUDE <= lon <= MAX_LONGITUDE
            ):
                bad.append(f"{r['unique_id']} ({prefix}): {lat}, {lon}")
    assert not bad, f"{len(bad)} row(s) geocoded outside the Nashville area: {bad[:10]}"


def test_public_export_redacts_owner_coordinates(
    public_rows: list[dict[str, str]],
) -> None:
    for column in OWNER_COORDINATE_COLUMNS:
        non_blank: int = sum(1 for r in public_rows if r[column])
        assert non_blank == 0, f"{non_blank} row(s) have a non-blank {column}"
