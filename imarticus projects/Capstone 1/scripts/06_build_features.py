"""Assemble the model matrix from the warehouse and cache it.

    .venv/Scripts/python.exe scripts/06_build_features.py

Reads fact_sample, fact_weather_daily, dim_cell and fact_landslide, runs them
through src.features.build, and writes data/processed/features.parquet.

The leakage audit at the end is the point of this script as much as the matrix
is. It checks the three ways this pipeline could quietly cheat:

  1. a feature that is constant within a label (a giveaway column)
  2. a feature correlating with the label far more than physics allows
  3. historical-event counts that include the sample's own event
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.features import build as features       # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("build_features")

SUSPICIOUS_CORRELATION = 0.60


def main() -> int:
    settings.ensure_dirs()

    samples = db.read_sql("""
        SELECT cell_id, date_id, label, stratum, event_id, split, region_block
        FROM   fact_sample
    """)
    if samples.empty:
        raise RuntimeError("fact_sample is empty — run scripts/04_build_sample.py")

    weather = db.read_sql("""
        SELECT cell_id, date_id, precip_mm, rain_mm, temp_max, temp_min, temp_mean,
               sm_0_7, sm_7_28, sm_28_100, et0_mm, wind_max, river_discharge
        FROM   fact_weather_daily
    """)
    if weather.empty:
        raise RuntimeError("fact_weather_daily is empty — run scripts/05_fetch_weather.py")

    cells = db.read_sql(f"""
        SELECT cell_id, lat_c, lon_c, {', '.join(features.STATIC_COLUMNS)}
        FROM   dim_cell
    """)
    events = db.read_sql("SELECT cell_id, date_id FROM fact_landslide")

    log.info("samples %d | weather rows %d | cells %d | events %d",
             len(samples), len(weather), len(cells), len(events))

    matrix = features.build_features(samples, weather, cells, events)

    numeric = features.feature_columns(matrix)
    log.info("%d model features", len(numeric))

    _audit(matrix, numeric)
    _persist(matrix, numeric)
    return 0


def _audit(matrix: pd.DataFrame, numeric: list[str]) -> None:
    """Checks that would otherwise surface as an implausibly good test score."""
    log.info("--- leakage audit ---")
    problems: list[str] = []

    constant = [
        column for column in numeric
        if matrix[column].nunique(dropna=True) <= 1
    ]
    if constant:
        log.warning("constant features (no signal, dropping later): %s", constant)

    correlations = (
        matrix[numeric]
        .corrwith(matrix["label"])
        .abs()
        .sort_values(ascending=False)
        .dropna()
    )
    log.info("top correlations with the label:")
    for column, value in correlations.head(12).items():
        flag = "  <-- suspicious" if value > SUSPICIOUS_CORRELATION else ""
        log.info("   %-26s %.3f%s", column, value, flag)

    suspicious = correlations[correlations > SUSPICIOUS_CORRELATION]
    if not suspicious.empty:
        problems.append(
            f"{len(suspicious)} features correlate above {SUSPICIOUS_CORRELATION} "
            f"with the label: {list(suspicious.index)}"
        )

    if "hist_events_before" in matrix.columns:
        cases = matrix[matrix["label"] == 1]
        # A case must never count itself. If the count were built from the whole
        # catalogue this would show up as every case having at least one.
        self_counted = (cases["hist_events_before"] > 0).mean()
        log.info("cases with a prior event in the same cell: %.1f%%",
                 100 * self_counted)
        if self_counted > 0.95:
            problems.append(
                "nearly every case has a prior event — the historical count is "
                "probably including the event itself"
            )

    missing = matrix[numeric].isna().mean().sort_values(ascending=False)
    heavy = missing[missing > 0.30]
    if not heavy.empty:
        log.warning("features missing in over 30%% of rows:")
        for column, share in heavy.items():
            log.warning("   %-26s %.1f%%", column, 100 * share)

    if problems:
        for problem in problems:
            log.error("AUDIT: %s", problem)
        raise RuntimeError("leakage audit failed — fix before training")

    log.info("leakage audit passed")


def _persist(matrix: pd.DataFrame, numeric: list[str]) -> None:
    path = settings.PROCESSED_DIR / "features.parquet"
    matrix.to_parquet(path, index=False)
    log.info("wrote %s (%d rows x %d columns)", path.name, *matrix.shape)

    manifest = settings.PROCESSED_DIR / "feature_columns.txt"
    manifest.write_text("\n".join(numeric), encoding="utf-8")
    log.info("wrote %s", manifest.name)

    log.info("--- rows per split ---")
    for split, group in matrix.groupby("split"):
        positives = int(group["label"].sum())
        log.info("%-8s %7d rows, %5d positives (%.2f%%)",
                 split, len(group), positives, 100 * positives / len(group))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log.error("feature build failed: %s", exc)
        raise
