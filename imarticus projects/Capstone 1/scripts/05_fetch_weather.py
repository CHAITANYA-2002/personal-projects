"""Fetch the weather windows the sample needs, then load fact_weather_daily.

    .venv/Scripts/python.exe scripts/05_fetch_weather.py --plan
    .venv/Scripts/python.exe scripts/05_fetch_weather.py
    .venv/Scripts/python.exe scripts/05_fetch_weather.py --load-only

Run --plan first. It prints the API cost before anything is spent, and the free
tier allows 10,000 calls a day — a fetch that needs 40,000 is a four-day job and
it is better to know that before starting than after.

Safe to interrupt. Completed windows are cached to Parquet and skipped on the
next run, and the daily spend is tracked across runs so a resumed fetch does not
overdraw the allowance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import openmeteo as om             # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("fetch_weather")

WEATHER_COLUMNS = [
    "cell_id", "date_id", "precip_mm", "rain_mm",
    "temp_max", "temp_min", "temp_mean",
    "sm_0_7", "sm_7_28", "sm_28_100",
    "et0_mm", "wind_max", "river_discharge",
]


def main(
    plan_only: bool,
    load_only: bool,
    no_discharge: bool,
    limit: int | None,
    daemon_hours: float = 0.0,
) -> int:
    settings.ensure_dirs()

    if not load_only:
        plans = _build_plan(limit, include_discharge=not no_discharge)
        if plan_only:
            return 0
        if not plans:
            log.info("every planned window is already cached")
        elif daemon_hours > 0:
            _run_until_done(plans, include_discharge=not no_discharge,
                            deadline_hours=daemon_hours)
        else:
            fetcher = om.WeatherFetcher()
            _report_budget(fetcher)
            fetcher.fetch_many(plans, include_discharge=not no_discharge)

    _load_to_warehouse()
    return 0


# Open-Meteo's allowance refills on a rolling window, so a run that stops on a
# 429 can simply wait rather than exit. Long enough to accumulate a useful slice
# of quota, short enough that a finished backfill is noticed promptly.
_RETRY_SLEEP_SECONDS = 20 * 60


def _run_until_done(
    plans: list[om.CellRequest],
    include_discharge: bool,
    deadline_hours: float,
) -> None:
    """Keep fetching across rate limits until the backfill is done.

    A single pass gets roughly 190 windows before the quota refuses, and the
    full backfill is tens of thousands. Driving that from outside means one
    wake-up per 190 windows for days. Sleeping through the refill inside one
    long-lived process is the same work with none of the supervision.
    """
    import time

    started = time.monotonic()
    deadline = deadline_hours * 3600
    cycle = 0

    while time.monotonic() - started < deadline:
        cycle += 1
        fetcher = om.WeatherFetcher()
        outstanding = fetcher.pending(plans)

        if not outstanding:
            log.info("backfill complete after %d cycles", cycle)
            return

        log.info("cycle %d: %d windows outstanding, %.1f hours elapsed",
                 cycle, len(outstanding), (time.monotonic() - started) / 3600)

        written = fetcher.fetch_many(outstanding, include_discharge=include_discharge)
        if written == 0:
            log.info("no progress this cycle — quota is spent")

        remaining = deadline - (time.monotonic() - started)
        if remaining <= _RETRY_SLEEP_SECONDS:
            break
        log.info("waiting %d minutes for the quota to refill",
                 _RETRY_SLEEP_SECONDS // 60)
        time.sleep(_RETRY_SLEEP_SECONDS)

    log.info("stopping after %.1f hours — rerun to continue",
             (time.monotonic() - started) / 3600)


# Fetch order. Cases first, then the controls that do the most work: a
# temporal control asks why THIS day failed when others in the same cell did
# not, which is the comparison the model learns most from. Background draws are
# last because they mostly re-establish the base rate.
STRATUM_PRIORITY = {"case": 0, "temporal": 1, "spatial": 2, "background": 3}


def _build_plan(
    limit: int | None = None,
    include_discharge: bool = True,
) -> list[om.CellRequest]:
    samples = db.read_sql("SELECT cell_id, date_id, stratum FROM fact_sample")
    if samples.empty:
        raise RuntimeError(
            "fact_sample is empty — run scripts/04_build_sample.py first"
        )

    # A full fetch costs well over the daily free allowance, so the plan is
    # ordered and truncated to what today can pay for. Successive runs extend
    # coverage and the model retrains on more data — the truncation is stated,
    # never silent.
    #
    # The ordering interleaves strata rather than running through cases first.
    # Fetching all cases before any control would make the first truncated run
    # a dataset of pure positives, which cannot train anything. Ranking within
    # each stratum and sorting on that rank means any prefix of the plan holds
    # roughly the designed case-to-control mix.
    samples["priority"] = samples["stratum"].map(STRATUM_PRIORITY).fillna(9)
    samples["rank_in_stratum"] = samples.groupby("stratum").cumcount()
    stratum_sizes = samples["stratum"].map(samples["stratum"].value_counts())
    samples["progress"] = samples["rank_in_stratum"] / stratum_sizes
    samples = samples.sort_values(["progress", "priority"])

    cells = db.read_sql("SELECT cell_id, lat_c, lon_c FROM dim_cell")
    plans = om.plan_requests(samples, cells)

    fetcher = om.WeatherFetcher()
    remaining = fetcher.pending(plans)

    summary = om.summarise_plan(
        plans, settings.OPENMETEO_BATCH_SIZE, include_discharge
    )
    log.info("--- full fetch plan ---")
    for key, value in summary.items():
        log.info("%-16s %s", key, value)
    log.info("%d of %d windows still need fetching", len(remaining), len(plans))

    if limit is not None and len(remaining) > limit:
        log.warning(
            "limiting this run to %d of %d outstanding windows — rerun to "
            "extend coverage; nothing already fetched is refetched",
            limit, len(remaining),
        )
        remaining = remaining[:limit]

    if remaining:
        cost = om.summarise_plan(
            remaining, settings.OPENMETEO_BATCH_SIZE, include_discharge
        )
        log.info(
            "this run: %d windows in %d requests, ~%.0f calls "
            "(%.2f days of budget)",
            cost["windows"], cost["http_requests"], cost["total_calls"],
            cost["days_at_budget"],
        )

    return remaining


def _report_budget(fetcher: om.WeatherFetcher) -> None:
    log.info("budget: %.0f spent today, %.0f remaining of %d",
             fetcher.budget.spent, fetcher.budget.remaining, fetcher.budget.limit)


def _load_to_warehouse() -> None:
    fetcher = om.WeatherFetcher()
    frame = fetcher.load_all_cached()
    if frame.empty:
        log.warning("no cached weather to load")
        return

    valid_cells = set(
        db.read_sql("SELECT cell_id FROM dim_cell")["cell_id"].tolist()
    )
    # Clamped to the study window, not simply to dim_date. 08_score.py extends
    # dim_date with forecast days so today's predictions have something to join
    # to, which makes dim_date a wider gate than this table should ever pass:
    # fact_weather_daily is the archive backfill and nothing else, and a
    # provisional near-real-time row landing in it would be indistinguishable
    # from reanalysed history.
    valid_dates = set(
        db.read_sql(
            "SELECT date_id FROM dim_date WHERE date_id BETWEEN :start AND :end",
            params={
                "start": int(settings.DATE_START.strftime("%Y%m%d")),
                "end": int(settings.DATE_END.strftime("%Y%m%d")),
            },
        )["date_id"].tolist()
    )

    before = len(frame)
    frame = frame[
        frame["cell_id"].isin(valid_cells) & frame["date_id"].isin(valid_dates)
    ]
    if len(frame) < before:
        log.info("dropped %d rows outside the study grid or calendar",
                 before - len(frame))

    for column in WEATHER_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA

    db.truncate(("fact_weather_daily",))
    db.write_frame(frame[WEATHER_COLUMNS], "fact_weather_daily", chunksize=10_000)
    _verify()


def _verify() -> None:
    stats = db.read_sql("""
        SELECT  COUNT(*)                                   AS n_rows,
                COUNT(DISTINCT cell_id)                    AS cells,
                MIN(date_id)                               AS first_day,
                MAX(date_id)                               AS last_day,
                ROUND(AVG(precip_mm), 2)                   AS mean_precip,
                ROUND(MAX(precip_mm), 1)                   AS max_precip,
                SUM(precip_mm IS NULL)                     AS null_precip,
                SUM(sm_0_7 IS NULL)                        AS null_soil,
                SUM(river_discharge IS NULL)               AS null_discharge
        FROM    fact_weather_daily
    """)
    log.info("--- fact_weather_daily ---")
    for column in stats.columns:
        log.info("%-16s %s", column, stats.iloc[0][column])

    coverage = db.read_sql("""
        SELECT  COUNT(*)                                   AS samples,
                SUM(w.cell_id IS NOT NULL)                 AS with_weather
        FROM        fact_sample s
        LEFT JOIN   fact_weather_daily w
               ON   w.cell_id = s.cell_id AND w.date_id = s.date_id
    """)
    samples = int(coverage.iloc[0]["samples"])
    covered = int(coverage.iloc[0]["with_weather"])
    log.info("sample coverage  %d of %d (%.1f%%)",
             covered, samples, 100 * covered / max(1, samples))

    if covered < samples:
        log.warning(
            "%d samples have no weather row for their own date — rerun the "
            "fetch, or they will be dropped when features are built",
            samples - covered,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true",
                        help="print the API cost and exit without fetching")
    parser.add_argument("--load-only", action="store_true",
                        help="skip fetching, just load what is already cached")
    parser.add_argument("--no-discharge", action="store_true",
                        help="skip the flood endpoint (halves the call cost)")
    parser.add_argument("--limit", type=int, default=None,
                        help="fetch at most this many outstanding windows")
    parser.add_argument("--daemon-hours", type=float, default=0.0,
                        help="keep retrying across rate limits for this many "
                             "hours instead of stopping at the first refusal")
    args = parser.parse_args()

    try:
        sys.exit(main(plan_only=args.plan, load_only=args.load_only,
                      no_discharge=args.no_discharge, limit=args.limit,
                      daemon_hours=args.daemon_hours))
    except KeyboardInterrupt:
        log.warning("interrupted — cached windows are kept, rerun to resume")
        sys.exit(130)
    except Exception as exc:
        log.error("weather fetch failed: %s", exc)
        raise
