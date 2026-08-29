"""Download the OSM extracts and compute per-cell exposure.

    .venv/Scripts/python.exe scripts/09_build_exposure.py
    .venv/Scripts/python.exe scripts/09_build_exposure.py --skip-download

Populates fact_exposure: road length, settlements, estimated population,
schools and health facilities per grid cell. This is what converts a risk
probability into a dispatch decision, so it is worth the disk it costs.

Independent of the weather and model path — safe to run at any time.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import osm                         # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("build_exposure")

EXPOSURE_COLUMNS = [
    "cell_id", "road_km_total", "road_km_primary", "bridges",
    "settlements", "schools", "health_facilities", "est_population",
]


def main(skip_download: bool) -> int:
    settings.ensure_dirs()

    if skip_download:
        directories = [
            osm.extract_dir(url) for url in settings.GEOFABRIK_EXTRACTS
        ]
        directories = [path for path in directories if path.exists()]
        if not directories:
            log.error("no extracted OSM data found — drop --skip-download")
            return 1
    else:
        directories = osm.download_extracts()

    frame = osm.build_exposure(directories)
    if frame.empty:
        log.error("no exposure rows produced")
        return 1

    _load_settlements(directories)

    frame = _restrict_to_grid(frame)
    _persist(frame)
    _report()
    return 0


def _load_settlements(directories) -> None:
    """Named settlements, so an exposure count can be turned back into places."""
    settlements = osm.settlement_records(directories)
    if settlements.empty:
        log.warning("no named settlements found — dim_settlement left empty")
        return

    known = set(db.read_sql("SELECT cell_id FROM dim_cell")["cell_id"].tolist())
    settlements = settlements[settlements["cell_id"].isin(known)]

    db.truncate(("dim_settlement",))
    db.write_frame(settlements, "dim_settlement", chunksize=5_000)


def _restrict_to_grid(frame: pd.DataFrame) -> pd.DataFrame:
    known = set(db.read_sql("SELECT cell_id FROM dim_cell")["cell_id"].tolist())
    before = len(frame)
    frame = frame[frame["cell_id"].isin(known)]
    if len(frame) < before:
        log.info("dropped %d cells outside the study grid", before - len(frame))

    for column in EXPOSURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0

    numeric = [column for column in EXPOSURE_COLUMNS if column != "cell_id"]
    frame[numeric] = frame[numeric].fillna(0)
    return frame[EXPOSURE_COLUMNS]


def _persist(frame: pd.DataFrame) -> None:
    frame.to_parquet(settings.INTERIM_DIR / "exposure.parquet", index=False)
    db.truncate(("fact_exposure",))
    db.write_frame(frame, "fact_exposure", chunksize=5_000)


def _report() -> None:
    stats = db.read_sql("""
        SELECT  COUNT(*)                         AS cells,
                ROUND(SUM(road_km_total), 0)     AS road_km,
                ROUND(SUM(road_km_primary), 0)   AS primary_km,
                SUM(settlements)                 AS settlements,
                SUM(schools)                     AS schools,
                SUM(health_facilities)           AS health,
                SUM(est_population)              AS population
        FROM    fact_exposure
    """)
    log.info("--- fact_exposure ---")
    for column in stats.columns:
        log.info("%-14s %s", column, stats.iloc[0][column])

    # COALESCE matters: SUM over an empty set is NULL, and this script can run
    # before the terrain build has flagged any hill cells.
    overlap = db.read_sql("""
        SELECT  COUNT(*)                                  AS hill_cells,
                COALESCE(SUM(x.cell_id IS NOT NULL), 0)   AS with_exposure
        FROM        dim_cell c
        LEFT JOIN   fact_exposure x ON x.cell_id = c.cell_id
        WHERE       c.is_hill = 1
    """)
    hill = int(overlap.iloc[0]["hill_cells"] or 0)
    covered = int(overlap.iloc[0]["with_exposure"] or 0)
    if hill == 0:
        log.info(
            "no hill cells flagged yet — run scripts/03_build_terrain.py, then "
            "this coverage figure becomes meaningful"
        )
    else:
        log.info("hill cells with exposure  %d of %d (%.1f%%)",
                 covered, hill, 100 * covered / hill)

    top = db.read_sql("""
        SELECT      c.state_name, c.lat_c, c.lon_c,
                    x.road_km_total, x.settlements, x.est_population
        FROM        fact_exposure x
        JOIN        dim_cell c ON c.cell_id = x.cell_id
        WHERE       c.is_hill = 1
        ORDER BY    x.est_population DESC
        LIMIT       8
    """)
    if not top.empty:
        log.info("--- most exposed hill cells ---\n%s", top.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true",
                        help="use extracts already on disk")
    args = parser.parse_args()

    try:
        sys.exit(main(skip_download=args.skip_download))
    except Exception as exc:
        log.error("exposure build failed: %s", exc)
        raise
