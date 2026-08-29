"""Download the NASA landslide catalogue and load study-area events.

    python scripts/01_load_landslides.py
    python scripts/01_load_landslides.py --force-download

Prints the per-state breakdown afterwards. Check it: the counts are the
training set, and a state that looks suspiciously small usually means a
spelling variant slipped past normalisation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import landslides                  # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("load_landslides")


def main(force_download: bool) -> int:
    settings.ensure_dirs()

    landslides.download(force=force_download)
    events = landslides.prepare_events()

    if events.empty:
        log.error("no events survived preparation — nothing to load")
        return 1

    interim_path = settings.INTERIM_DIR / "events_study_area.parquet"
    events.to_parquet(interim_path, index=False)
    log.info("cached prepared events to %s", interim_path.name)

    # Cells the events land in must exist in dim_cell or the FK will reject.
    known_cells = set(
        db.read_sql("SELECT cell_id FROM dim_cell")["cell_id"].tolist()
    )
    unknown = sorted(set(events["cell_id"]) - known_cells)
    if unknown:
        raise RuntimeError(
            f"{len(unknown)} event cells are missing from dim_cell "
            f"(first: {unknown[:5]}). Run scripts/00_init_db.py first."
        )

    db.truncate(("fact_landslide",))
    db.write_frame(events, "fact_landslide")

    _report(events)
    return 0


def _report(events) -> None:
    by_country = landslides.summarise(events, by="country_name")
    log.info("--- events by country ---")
    for row in by_country.itertuples(index=False):
        log.info("%6d  %s", row.events, row.country_name)

    india = events[events["country_name"] == settings.EVALUATION_COUNTRY]
    summary = landslides.summarise(india)

    log.info("--- %s, by state ---", settings.EVALUATION_COUNTRY)
    for row in summary.itertuples(index=False):
        log.info("%6d  %s", row.events, row.state_name_norm)

    ner = int(summary.loc[
        summary["state_name_norm"].isin(settings.NER_STATES), "events"
    ].sum())
    west = int(summary.loc[
        summary["state_name_norm"].isin(settings.WEST_HIMALAYAN_STATES), "events"
    ].sum())

    log.info("--- totals ---")
    log.info("north eastern region   %6d", ner)
    log.info("western himalaya       %6d", west)
    log.info("%-22s %6d", f"{settings.EVALUATION_COUNTRY} (evaluation)", len(india))
    log.info("full arc (training)    %6d", len(events))
    log.info("distinct cells         %6d", events["cell_id"].nunique())
    log.info("date range             %s .. %s",
             events["event_date"].min(), events["event_date"].max())

    rainfall_triggers = {"downpour", "continuous_rain", "rain", "monsoon",
                         "tropical_cyclone", "flooding", "snowfall_snowmelt"}
    rain_share = events["landslide_trigger"].isin(rainfall_triggers).mean()
    log.info("rainfall-triggered     %5.1f%%", rain_share * 100)

    median_accuracy = events["loc_accuracy_km"].median()
    log.info("median location error  %5.1f km", median_accuracy)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-download", action="store_true",
        help="re-download the catalogue even if a copy already exists",
    )
    args = parser.parse_args()

    try:
        sys.exit(main(force_download=args.force_download))
    except Exception as exc:
        log.error("load failed: %s", exc)
        raise
