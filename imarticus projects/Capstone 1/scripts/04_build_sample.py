"""Draw the case-control modelling frame and load it into fact_sample.

    .venv/Scripts/python.exe scripts/04_build_sample.py
    .venv/Scripts/python.exe scripts/04_build_sample.py --ratio 20 --seed 7

Requires the hill mask, so run scripts/03_build_terrain.py first. Sampling from
the full grid instead of hill cells would let the model win by learning
"mountain versus plain", which is true and useless.

The composition it prints is not decoration — it is the thing to check before
anything downstream is trusted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data.sampling import CaseControlSampler # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("build_sample")

# Leave-one-region-out folds. Kashmir is deliberately its own block: only 47% of
# its events fall in the monsoon against 85-93% elsewhere, because it is driven
# by western disturbances and snowmelt. A model that generalises to Kashmir has
# learned conditions rather than a calendar.
REGION_BLOCKS = {
    "west_himalaya": ("Kashmir", "Himachal Pradesh"),
    "central_himalaya": ("Uttarakhand", "Uttar Pradesh"),
    "eastern_himalaya": ("Sikkim", "Bengal", "West Bengal", "Arunachal Pradesh"),
    "north_east": ("Assam", "Nagaland", "Manipur", "Meghalaya", "Mizoram", "Tripura"),
}


def main(ratio: int | None, seed: int) -> int:
    events = _load_events()
    hill_cells = _load_hill_cells()
    # Bounded to the study window on purpose. dim_date is not a pure
    # dimension: 08_score.py appends forecast days to it so today's scores have
    # something to join to, and an unbounded read let those days into the
    # control pool. The sampler then asked Open-Meteo's archive for windows
    # ending in the present, which ERA5 does not have (~5-day lag) and which it
    # answers with HTTP 400.
    dates = db.read_sql(
        "SELECT date_id, day_of_year FROM dim_date "
        "WHERE date_id BETWEEN :start AND :end",
        params={
            "start": int(settings.DATE_START.strftime("%Y%m%d")),
            "end": int(settings.DATE_END.strftime("%Y%m%d")),
        },
    )

    log.info("events %d | hill cells %d | dates %d",
             len(events), len(hill_cells), len(dates))

    usable = events["cell_id"].isin(set(hill_cells["cell_id"]))
    if (~usable).any():
        log.warning(
            "%d events sit in cells the hill mask rejected — keeping them as "
            "cases anyway, since a recorded landslide is evidence the terrain "
            "matters regardless of mean slope",
            int((~usable).sum()),
        )

    sampler = CaseControlSampler(events, hill_cells, dates, seed=seed)
    frame = sampler.build(negatives_per_positive=ratio)
    frame = _assign_region_blocks(frame, events)

    _persist(frame)
    _verify(frame)
    return 0


def _load_events() -> pd.DataFrame:
    events = db.read_sql("""
        SELECT event_id, cell_id, date_id, event_date,
               latitude, longitude, state_name_norm, country_name
        FROM   fact_landslide
    """)
    if events.empty:
        raise RuntimeError(
            "fact_landslide is empty — run scripts/01_load_landslides.py first"
        )
    return events


def _load_hill_cells() -> pd.DataFrame:
    cells = db.read_sql("""
        SELECT cell_id, lat_c, lon_c, slope_mean, elev_mean
        FROM   dim_cell
        WHERE  is_hill = 1
    """)
    if cells.empty:
        raise RuntimeError(
            "no cells are flagged is_hill — run scripts/03_build_terrain.py first"
        )
    return cells


def _assign_region_blocks(frame: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Tag each sample with a spatial fold, taken from its nearest known event.

    Cases inherit their own event's state. Controls inherit the block of the
    case cell they were drawn around, falling back to a latitude/longitude rule
    for background draws that belong to no case.
    """
    state_to_block = {
        state: block
        for block, states in REGION_BLOCKS.items()
        for state in states
    }

    case_blocks = (
        events.assign(block=events["state_name_norm"].map(state_to_block))
        .dropna(subset=["block"])
        .drop_duplicates(subset="cell_id")
        .set_index("cell_id")["block"]
    )

    frame = frame.copy()
    frame["region_block"] = frame["cell_id"].map(case_blocks)

    missing = frame["region_block"].isna()
    if missing.any():
        centroids = db.read_sql(
            "SELECT cell_id, lat_c, lon_c FROM dim_cell"
        ).set_index("cell_id")
        lon = frame.loc[missing, "cell_id"].map(centroids["lon_c"])
        frame.loc[missing, "region_block"] = pd.cut(
            lon,
            bins=[-999, 78.0, 82.0, 90.0, 999],
            labels=["west_himalaya", "central_himalaya",
                    "eastern_himalaya", "north_east"],
        ).astype(str)

    log.info("--- region blocks ---")
    for block, count in frame["region_block"].value_counts().items():
        log.info("%-18s %7d", block, count)
    return frame


def _persist(frame: pd.DataFrame) -> None:
    cached = settings.INTERIM_DIR / "sample.parquet"
    frame.to_parquet(cached, index=False)
    log.info("cached sample to %s", cached.name)

    columns = ["cell_id", "date_id", "label", "stratum",
               "event_id", "split", "region_block"]
    db.truncate(("fact_sample",))
    db.write_frame(frame[columns], "fact_sample")


def _verify(frame: pd.DataFrame) -> None:
    """Assertions that would otherwise fail silently three phases later."""
    problems: list[str] = []

    duplicates = frame.duplicated(subset=["cell_id", "date_id"]).sum()
    if duplicates:
        problems.append(f"{duplicates} duplicate cell-date pairs")

    cases = frame[frame["label"] == 1]
    if cases["event_id"].isna().any():
        problems.append("cases with no event_id")
    if frame[frame["label"] == 0]["event_id"].notna().any():
        problems.append("controls carrying an event_id")

    counts = db.read_sql("""
        SELECT split, label, COUNT(*) AS n_rows
        FROM   fact_sample
        GROUP  BY split, label
        ORDER  BY split, label
    """)
    log.info("--- fact_sample as loaded ---")
    log.info("\n%s", counts.to_string(index=False))

    for split in ("train", "val", "test"):
        subset = counts[counts["split"] == split]
        if subset.empty or 1 not in set(subset["label"]):
            problems.append(f"split {split!r} has no positives")

    if problems:
        for problem in problems:
            log.error("CHECK FAILED: %s", problem)
        raise RuntimeError(f"{len(problems)} sample checks failed")

    log.info("all sample checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratio", type=int, default=None,
                        help="negatives per positive (default from settings)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        sys.exit(main(ratio=args.ratio, seed=args.seed))
    except Exception as exc:
        log.error("sample build failed: %s", exc)
        raise
