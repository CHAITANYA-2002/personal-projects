"""Attach state and district names to every grid cell.

    .venv/Scripts/python.exe scripts/11_label_admin.py

Without this, dim_cell.district_name is null and mart_district_daily_risk
returns nothing — the command centre would show an empty priority table and it
would look like a quiet day rather than a missing join.

Run after the grid exists. Independent of weather and the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import admin                       # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("label_admin")

STAGING_TABLE = "_stg_admin"


def main() -> int:
    settings.ensure_dirs()

    cells = db.read_sql("SELECT cell_id, lat_c, lon_c FROM dim_cell")
    if cells.empty:
        raise RuntimeError("dim_cell is empty — run scripts/00_init_db.py first")

    log.info("labelling %d cells", len(cells))
    labels = admin.label_cells(cells)

    labelled = labels.dropna(subset=[c for c in admin.LEVELS.values()
                                     if c in labels.columns], how="all")
    if labelled.empty:
        log.error("no cells matched any boundary")
        return 1

    _load(labelled)
    _report()
    return 0


def _load(labels: pd.DataFrame) -> None:
    engine = db.get_engine()
    with engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS `{STAGING_TABLE}`"))
        conn.commit()

    db.write_frame(labels, STAGING_TABLE, if_exists="replace")

    columns = [c for c in admin.LEVELS.values() if c in labels.columns]
    assignments = ", ".join(f"c.{column} = s.{column}" for column in columns)

    with engine.connect() as conn:
        conn.execute(text(
            f"ALTER TABLE `{STAGING_TABLE}` ADD PRIMARY KEY (cell_id)"
        ))
        result = conn.execute(text(
            f"UPDATE dim_cell c JOIN `{STAGING_TABLE}` s "
            f"ON s.cell_id = c.cell_id SET {assignments}"
        ))
        conn.commit()
        log.info("updated %d rows in dim_cell", result.rowcount)

        conn.execute(text(f"DROP TABLE IF EXISTS `{STAGING_TABLE}`"))
        conn.commit()


def _report() -> None:
    coverage = db.read_sql("""
        SELECT  COUNT(*)                                        AS cells,
                SUM(state_name IS NOT NULL)                     AS with_state,
                SUM(district_name IS NOT NULL)                  AS with_district,
                COUNT(DISTINCT state_name)                      AS states,
                COUNT(DISTINCT district_name)                   AS districts
        FROM    dim_cell
        WHERE   is_hill = 1
    """)
    log.info("--- hill cells ---")
    for column in coverage.columns:
        log.info("%-16s %s", column, coverage.iloc[0][column])

    top = db.read_sql("""
        SELECT      c.state_name, COUNT(DISTINCT c.district_name) AS districts,
                    COUNT(*)                                      AS hill_cells,
                    COUNT(f.event_id)                             AS events
        FROM        dim_cell c
        LEFT JOIN   fact_landslide f ON f.cell_id = c.cell_id
        WHERE       c.is_hill = 1 AND c.state_name IS NOT NULL
        GROUP BY    c.state_name
        ORDER BY    events DESC
        LIMIT       12
    """)
    if not top.empty:
        log.info("--- states by recorded events ---\n%s",
                 top.to_string(index=False))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log.error("admin labelling failed: %s", exc)
        raise
