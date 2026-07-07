"""Integration test against a real, disposable PostgreSQL container.

Demonstrates the pattern for testing SQL transformation logic without
touching a developer's local server. Skipped automatically when Docker or
testcontainers is unavailable (e.g. plain unit-test runs).

Open-source stack: testcontainers-python + the official postgres image.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, text

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer


@pytest.fixture(scope="module")
def pg_engine() -> Engine:  # type: ignore[misc]
    try:
        container = PostgresContainer("postgres:17")
        container.start()
    except Exception as exc:  # Docker not available
        pytest.skip(f"Docker unavailable: {exc}")
    engine: Engine = create_engine(container.get_connection_url(driver="psycopg"))
    yield engine
    engine.dispose()
    container.stop()


def test_duplicate_removal_keeps_lowest_unique_id(pg_engine: Engine) -> None:
    """Mirrors the ROW_NUMBER() de-duplication in src/cleaning.sql."""
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE housing_data (
                    unique_id int PRIMARY KEY,
                    parcel_id text, property_address text,
                    sale_price numeric, sale_date date, legal_reference text
                );
                INSERT INTO housing_data VALUES
                  (1, 'P1', '1808 FOX CHASE DR', 240000, '2016-01-01', 'L1'),
                  (2, 'P1', '1808 FOX CHASE DR', 240000, '2016-01-01', 'L1'),
                  (3, 'P2', '410 ROSEHILL CT',   120000, '2016-02-01', 'L2');
                """
            )
        )
        conn.execute(
            text(
                """
                DELETE FROM housing_data
                WHERE unique_id IN (
                    SELECT unique_id FROM (
                        SELECT unique_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY parcel_id, property_address,
                                                sale_price, sale_date, legal_reference
                                   ORDER BY unique_id
                               ) AS rn
                        FROM housing_data
                    ) ranked
                    WHERE rn > 1
                );
                """
            )
        )
        remaining: list[int] = [
            row[0]
            for row in conn.execute(text("SELECT unique_id FROM housing_data ORDER BY unique_id"))
        ]
    assert remaining == [1, 3]
