"""Create the warehouse and load the two static dimensions.

    python scripts/00_init_db.py
    python scripts/00_init_db.py --reset    # drop dimension rows and reload

Safe to re-run: the schema uses CREATE TABLE IF NOT EXISTS, and the dimensions
are skipped when already populated unless --reset is passed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import grid                        # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("init_db")

DIMENSION_TABLES = ("dim_date", "dim_cell")
ALL_TABLES = (
    "dim_date", "dim_cell", "dim_road", "dim_settlement",
    "fact_landslide", "fact_weather_daily", "fact_sample",
    "fact_exposure", "fact_risk_pred", "etl_run_log",
)


def main(reset: bool) -> int:
    settings.ensure_dirs()

    log.info("target: %s@%s:%s/%s",
             settings.MYSQL_USER, settings.MYSQL_HOST,
             settings.MYSQL_PORT, settings.MYSQL_DATABASE)

    db.ensure_database()
    db.run_sql_file(settings.SQL_DIR / "01_schema.sql")

    if reset:
        # Children first: fact_landslide references both dimensions.
        db.truncate(("fact_landslide", "fact_sample", "fact_exposure",
                     "fact_risk_pred", "fact_weather_daily",
                     "dim_cell", "dim_date"))

    counts = db.table_counts(DIMENSION_TABLES)

    if counts["dim_date"] == 0:
        db.write_frame(grid.build_date_frame(), "dim_date")
    else:
        log.info("dim_date already holds %d rows — skipping", counts["dim_date"])

    if counts["dim_cell"] == 0:
        db.write_frame(grid.build_grid_frame(), "dim_cell")
    else:
        log.info("dim_cell already holds %d rows — skipping", counts["dim_cell"])

    log.info("--- row counts ---")
    for table, count in db.table_counts(ALL_TABLES).items():
        log.info("%-22s %10d", table, count)

    log.info("warehouse ready")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true",
        help="truncate all tables before loading the dimensions",
    )
    args = parser.parse_args()

    try:
        sys.exit(main(reset=args.reset))
    except Exception as exc:  # surfaced with context, not a bare traceback
        log.error("initialisation failed: %s", exc)
        raise
