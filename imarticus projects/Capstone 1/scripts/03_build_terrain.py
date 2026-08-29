"""Compute per-cell terrain from the DEM tiles and update dim_cell.

    python scripts/03_build_terrain.py
    python scripts/03_build_terrain.py --rebuild     # ignore the cache

Reads each tile once, derives elevation, slope, aspect and ruggedness for the
100 cells it contains, and writes the results back into dim_cell. Cells whose
mean slope clears the threshold in settings are flagged is_hill — this is the
mask the negative sampler draws from, and it is the reason the model cannot
win by learning "mountain versus plain".

Progress is checkpointed to Parquet every few tiles, so an interrupted run
resumes without reprocessing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import dem, grid                   # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("build_terrain")

CHECKPOINT_EVERY = 20
STAGING_TABLE = "_stg_terrain"

# Above this share of missing tiles the hill mask reflects download failures
# rather than terrain, so the run stops instead of producing a plausible lie.
MAX_MISSING_SHARE = 0.02

TERRAIN_COLUMNS = (
    "elev_mean", "elev_min", "elev_max", "elev_range",
    "slope_mean", "slope_max", "slope_std",
    "aspect_sin", "aspect_cos", "tri",
)


def main(rebuild: bool) -> int:
    settings.ensure_dirs()
    checkpoint = settings.INTERIM_DIR / "cell_terrain.parquet"

    tiles = dem.tiles_for_study_area()
    missing = dem.missing_tiles(tiles)

    # A handful of missing tiles is survivable: their cells simply get null
    # terrain and fall out of the hill mask, which costs a little coverage.
    # Losing a large fraction is not, because the mask would then be shaped by
    # download luck rather than by slope.
    if missing:
        share = len(missing) / len(tiles)
        available = [tile for tile in tiles if tile.path.exists()]
        if share > MAX_MISSING_SHARE:
            log.error(
                "%d of %d tiles (%.1f%%) are not downloaded — over the %.0f%% "
                "limit. Run scripts/02_download_dem.py to finish.",
                len(missing), len(tiles), 100 * share, 100 * MAX_MISSING_SHARE,
            )
            return 1
        log.warning(
            "%d of %d tiles missing (%.1f%%) — continuing without them: %s",
            len(missing), len(tiles), 100 * share,
            ", ".join(tile.name for tile in missing[:5]),
        )
        tiles = available

    frame = _compute(tiles, checkpoint, rebuild)
    if frame.empty:
        log.error("no terrain rows produced")
        return 1

    frame = _attach_cell_ids(frame)
    _load(frame)
    _report()
    return 0


def _compute(tiles, checkpoint: Path, rebuild: bool) -> pd.DataFrame:
    done_tiles: set[str] = set()
    parts: list[pd.DataFrame] = []

    if checkpoint.exists() and not rebuild:
        cached = pd.read_parquet(checkpoint)
        parts.append(cached)
        done_tiles = set(cached["tile"].unique())
        log.info("resuming: %d tiles already processed", len(done_tiles))

    pending = [tile for tile in tiles if tile.name not in done_tiles]
    log.info("processing %d tiles", len(pending))

    for index, tile in enumerate(pending, start=1):
        try:
            stats = dem.terrain_for_tile(tile)
        except Exception as exc:
            log.error("tile %s failed: %s", tile.name, exc)
            continue

        if stats.empty:
            continue
        stats["tile"] = tile.name
        parts.append(stats)

        if index % CHECKPOINT_EVERY == 0 or index == len(pending):
            pd.concat(parts, ignore_index=True).to_parquet(checkpoint, index=False)
            log.info("%d/%d tiles | %d cells so far",
                     index, len(pending), sum(len(p) for p in parts))

    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _attach_cell_ids(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["cell_id"] = grid.assign_cells(frame, "lat_c", "lon_c")
    frame = frame.dropna(subset=["cell_id"])
    frame["cell_id"] = frame["cell_id"].astype("int64")

    duplicates = int(frame["cell_id"].duplicated().sum())
    if duplicates:
        # Tile edges overlap by one pixel row; keep the first observation.
        log.info("dropping %d duplicate cells at tile boundaries", duplicates)
        frame = frame.drop_duplicates(subset="cell_id", keep="first")

    frame["is_hill"] = (frame["slope_mean"] >= settings.MIN_SLOPE_DEG).astype(int)
    return frame[["cell_id", *TERRAIN_COLUMNS, "is_hill"]]


def _load(frame: pd.DataFrame) -> None:
    """Stage the terrain frame, then update dim_cell with one join."""
    engine = db.get_engine()

    with engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS `{STAGING_TABLE}`"))
        conn.commit()

    db.write_frame(frame, STAGING_TABLE, if_exists="replace")

    assignments = ", ".join(f"c.{col} = s.{col}" for col in TERRAIN_COLUMNS)
    with engine.connect() as conn:
        conn.execute(text(
            f"ALTER TABLE `{STAGING_TABLE}` ADD PRIMARY KEY (cell_id)"
        ))
        result = conn.execute(text(
            f"UPDATE dim_cell c "
            f"JOIN `{STAGING_TABLE}` s ON s.cell_id = c.cell_id "
            f"SET {assignments}, c.is_hill = s.is_hill"
        ))
        conn.commit()
        log.info("updated %d rows in dim_cell", result.rowcount)

        conn.execute(text(f"DROP TABLE IF EXISTS `{STAGING_TABLE}`"))
        conn.commit()


def _report() -> None:
    summary = db.read_sql("""
        SELECT  COUNT(*)                                    AS cells,
                SUM(slope_mean IS NOT NULL)                 AS with_terrain,
                SUM(is_hill)                                AS hill_cells,
                ROUND(AVG(slope_mean), 2)                   AS mean_slope,
                ROUND(MAX(slope_max), 2)                    AS steepest,
                ROUND(MAX(elev_max), 0)                     AS highest_point
        FROM    dim_cell
    """)
    log.info("--- dim_cell ---")
    for column in summary.columns:
        log.info("%-16s %s", column, summary.iloc[0][column])

    coverage = db.read_sql("""
        SELECT  c.is_hill,
                COUNT(*)                                    AS events
        FROM    fact_landslide f
        JOIN    dim_cell c ON c.cell_id = f.cell_id
        GROUP BY c.is_hill
    """)
    log.info("--- landslide events by hill mask ---")
    for row in coverage.itertuples(index=False):
        label = "hill" if row.is_hill else "not hill"
        log.info("%-10s %6d", label, row.events)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true",
                        help="ignore the checkpoint and recompute every tile")
    args = parser.parse_args()

    try:
        sys.exit(main(rebuild=args.rebuild))
    except KeyboardInterrupt:
        log.warning("interrupted — checkpoint kept, rerun to resume")
        sys.exit(130)
    except Exception as exc:
        log.error("terrain build failed: %s", exc)
        raise
