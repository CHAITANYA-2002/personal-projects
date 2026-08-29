"""Warehouse invariants. Skipped when MySQL is not reachable.

These are data tests, not unit tests. They exist because dim_date is not a pure
dimension: 08_score.py extends it with forecast days so the day's predictions
have something to join to. Anything that reads dim_date unbounded therefore
inherits future dates, which is exactly how eighteen samples came to be dated
2026 and how the archive backfill came to ask Open-Meteo for a window ERA5 has
a five-day lag on.
"""

from __future__ import annotations

import pytest

from config import settings

pytestmark = pytest.mark.warehouse


@pytest.fixture(scope="module")
def warehouse():
    from src import db
    try:
        db.read_sql("SELECT 1 AS ok")
    except Exception as exc:                              # pragma: no cover
        pytest.skip(f"warehouse unreachable: {exc}")
    return db


@pytest.fixture(scope="module")
def study_end() -> int:
    return int(settings.DATE_END.strftime("%Y%m%d"))


@pytest.fixture(scope="module")
def study_start() -> int:
    return int(settings.DATE_START.strftime("%Y%m%d"))


def test_no_sample_is_dated_outside_the_study_window(
    warehouse, study_start, study_end
):
    """The bug: forecast days leaked into the control pool via dim_date."""
    stray = warehouse.read_sql(
        "SELECT COUNT(*) AS n FROM fact_sample "
        "WHERE date_id < :start OR date_id > :end",
        params={"start": study_start, "end": study_end},
    )["n"][0]
    assert int(stray) == 0


def test_no_weather_row_is_dated_outside_the_study_window(
    warehouse, study_start, study_end
):
    """fact_weather_daily is the reanalysis backfill and nothing else.

    A provisional near-real-time row landing here would be indistinguishable
    from reanalysed history once loaded.
    """
    stray = warehouse.read_sql(
        "SELECT COUNT(*) AS n FROM fact_weather_daily "
        "WHERE date_id < :start OR date_id > :end",
        params={"start": study_start, "end": study_end},
    )["n"][0]
    assert int(stray) == 0


def test_samples_are_unique_on_cell_and_date(warehouse):
    dupes = warehouse.read_sql("""
        SELECT COUNT(*) AS n FROM (
            SELECT cell_id, date_id FROM fact_sample
            GROUP BY cell_id, date_id HAVING COUNT(*) > 1
        ) d
    """)["n"][0]
    assert int(dupes) == 0


def test_the_temporal_split_does_not_run_backwards(warehouse):
    """Train must end before val, val before test — no future in the past."""
    bounds = warehouse.read_sql("""
        SELECT split, MIN(date_id) AS lo, MAX(date_id) AS hi
        FROM   fact_sample GROUP BY split
    """).set_index("split")

    assert bounds.loc["train", "hi"] < bounds.loc["val", "lo"]
    assert bounds.loc["val", "hi"] < bounds.loc["test", "lo"]


def test_weather_keys_resolve_to_real_cells(warehouse):
    orphans = warehouse.read_sql("""
        SELECT COUNT(*) AS n
        FROM        fact_weather_daily w
        LEFT JOIN   dim_cell c ON c.cell_id = w.cell_id
        WHERE       c.cell_id IS NULL
    """)["n"][0]
    assert int(orphans) == 0
