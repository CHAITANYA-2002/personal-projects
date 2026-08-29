"""End-to-end verification of the Slopewatch pipeline.

    .venv/Scripts/python.exe scripts/10_verify.py

Runs every check that would otherwise surface as a confident wrong answer three
months later. Each check states what it is protecting against, prints PASS,
WARN or FAIL, and the script exits non-zero if anything fails.

The checks are grouped by what they defend:

  completeness   a stage that silently produced nothing
  integrity      keys that do not join, values outside physical range
  leakage        the model being handed the answer
  honesty        metrics that are too good, or reported from the wrong split
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("verify")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Check:
    group: str
    name: str
    status: str
    detail: str


results: list[Check] = []


def record(group: str, name: str, status: str, detail: str = "") -> None:
    results.append(Check(group, name, status, detail))
    marker = {PASS: "  ", WARN: "! ", FAIL: "X "}[status]
    log.info("%s%-11s %-42s %s", marker, status, name, detail)


def scalar(query: str, default=0):
    frame = db.read_sql(query)
    if frame.empty:
        return default
    value = frame.iloc[0, 0]
    return default if value is None else value


# --------------------------------------------------------------------------
# completeness
# --------------------------------------------------------------------------

EXPECTED_MINIMUMS = {
    "dim_date": 3650,
    "dim_cell": 40000,
    "fact_landslide": 1000,
    "fact_weather_daily": 10000,
    "fact_sample": 10000,
    "fact_exposure": 1000,
}


def check_completeness() -> None:
    log.info("--- completeness ---")
    for table, minimum in EXPECTED_MINIMUMS.items():
        try:
            count = int(scalar(f"SELECT COUNT(*) FROM {table}"))
        except Exception as exc:
            record("completeness", table, FAIL, f"unreadable: {exc}")
            continue

        if count == 0:
            record("completeness", table, FAIL, "empty")
        elif count < minimum:
            record("completeness", table, WARN,
                   f"{count:,} rows, expected at least {minimum:,}")
        else:
            record("completeness", table, PASS, f"{count:,} rows")

    hill = int(scalar("SELECT COUNT(*) FROM dim_cell WHERE is_hill = 1"))
    total = int(scalar("SELECT COUNT(*) FROM dim_cell"))
    share = hill / max(1, total)
    if hill == 0:
        record("completeness", "hill mask", FAIL, "no cells flagged is_hill")
    elif not 0.05 <= share <= 0.80:
        record("completeness", "hill mask", WARN,
               f"{hill:,} of {total:,} cells ({share:.1%}) — implausible share")
    else:
        record("completeness", "hill mask", PASS,
               f"{hill:,} of {total:,} cells ({share:.1%})")


# --------------------------------------------------------------------------
# integrity
# --------------------------------------------------------------------------

def check_integrity() -> None:
    log.info("--- integrity ---")

    orphans = int(scalar("""
        SELECT COUNT(*) FROM fact_weather_daily w
        LEFT JOIN dim_cell c ON c.cell_id = w.cell_id
        WHERE c.cell_id IS NULL
    """))
    record("integrity", "weather cell keys", FAIL if orphans else PASS,
           f"{orphans:,} orphan rows" if orphans else "all join to dim_cell")

    orphan_dates = int(scalar("""
        SELECT COUNT(*) FROM fact_weather_daily w
        LEFT JOIN dim_date d ON d.date_id = w.date_id
        WHERE d.date_id IS NULL
    """))
    record("integrity", "weather date keys", FAIL if orphan_dates else PASS,
           f"{orphan_dates:,} orphan rows" if orphan_dates else "all join to dim_date")

    duplicates = int(scalar("""
        SELECT COUNT(*) FROM (
            SELECT cell_id, date_id FROM fact_sample
            GROUP BY cell_id, date_id HAVING COUNT(*) > 1
        ) d
    """))
    record("integrity", "sample uniqueness", FAIL if duplicates else PASS,
           f"{duplicates:,} duplicated cell-dates" if duplicates
           else "one row per cell-date")

    # Rainfall outside this range is a unit error, not weather.
    absurd = int(scalar("""
        SELECT COUNT(*) FROM fact_weather_daily
        WHERE precip_mm < 0 OR precip_mm > 2000
    """))
    record("integrity", "rainfall range", FAIL if absurd else PASS,
           f"{absurd:,} rows outside 0-2000 mm" if absurd else "0-2000 mm")

    # Volumetric soil moisture is a fraction; above 1 means a scaling mistake.
    bad_soil = int(scalar("""
        SELECT COUNT(*) FROM fact_weather_daily
        WHERE sm_0_7 < 0 OR sm_0_7 > 1
    """))
    record("integrity", "soil moisture range", FAIL if bad_soil else PASS,
           f"{bad_soil:,} rows outside 0-1" if bad_soil else "0-1 m3/m3")

    steep = int(scalar("""
        SELECT COUNT(*) FROM dim_cell
        WHERE slope_mean IS NOT NULL AND (slope_mean < 0 OR slope_mean > 90)
    """))
    record("integrity", "slope range", FAIL if steep else PASS,
           f"{steep:,} cells outside 0-90 deg" if steep else "0-90 degrees")

    # default=None, not 0: a missing terrain build must read as "not built"
    # rather than as a study area at sea level.
    high = scalar("SELECT MAX(elev_max) FROM dim_cell", default=None)
    if high is None:
        record("integrity", "elevation sanity", WARN, "terrain not built")
    elif not 8000 <= float(high) <= 9000:
        record("integrity", "elevation sanity", WARN,
               f"highest point {float(high):.0f} m — expected Everest near 8850")
    else:
        record("integrity", "elevation sanity", PASS,
               f"highest point {float(high):.0f} m")


# --------------------------------------------------------------------------
# leakage
# --------------------------------------------------------------------------

def check_leakage() -> None:
    log.info("--- leakage ---")

    path = settings.PROCESSED_DIR / "features.parquet"
    if not path.exists():
        record("leakage", "feature matrix", WARN, "not built yet")
        return

    matrix = pd.read_parquet(path)
    manifest = settings.PROCESSED_DIR / "feature_columns.txt"
    features = manifest.read_text(encoding="utf-8").split() if manifest.exists() else []
    features = [column for column in features if column in matrix.columns]

    correlations = matrix[features].corrwith(matrix["label"]).abs().dropna()
    worst = correlations.sort_values(ascending=False).head(3)
    if (correlations > 0.60).any():
        record("leakage", "label correlation", FAIL,
               f"{list(worst.index)} above 0.60")
    else:
        record("leakage", "label correlation", PASS,
               f"max {worst.iloc[0]:.3f} ({worst.index[0]})")

    if "hist_events_before" in matrix.columns:
        cases = matrix[matrix["label"] == 1]
        share = float((cases["hist_events_before"] > 0).mean())
        if share > 0.95:
            record("leakage", "history counts", FAIL,
                   f"{share:.1%} of cases have a prior event — self-counting")
        else:
            record("leakage", "history counts", PASS,
                   f"{share:.1%} of cases have a prior event")

    # A split that mixes years is a random split wearing a costume.
    overlap = (
        matrix.groupby("split")["date_id"]
        .agg(["min", "max"])
        .sort_values("min")
    )
    ordered = overlap["max"].is_monotonic_increasing
    record("leakage", "temporal split", PASS if ordered else FAIL,
           " | ".join(f"{split} {row['min']}-{row['max']}"
                      for split, row in overlap.iterrows()))


# --------------------------------------------------------------------------
# honesty
# --------------------------------------------------------------------------

def check_metrics() -> None:
    log.info("--- honesty ---")

    path = settings.MODELS_DIR / "metrics_v1.json"
    if not path.exists():
        record("honesty", "model metrics", WARN, "model not trained yet")
        return

    report = json.loads(path.read_text(encoding="utf-8"))
    selected = report.get("selected")
    scores = report.get("scores", {}).get(selected, {})
    test = scores.get("test") or scores.get("val") or {}

    pr_auc = test.get("pr_auc")
    if pr_auc is None:
        record("honesty", "test pr_auc", WARN, "no test score recorded")
    elif pr_auc > 0.98:
        record("honesty", "test pr_auc", FAIL,
               f"{pr_auc:.3f} — implausibly high, suspect leakage")
    elif pr_auc < 0.15:
        record("honesty", "test pr_auc", WARN,
               f"{pr_auc:.3f} — barely above the base rate")
    else:
        record("honesty", "test pr_auc", PASS, f"{pr_auc:.3f}")

    recall = test.get("recall_at_5pct")
    if recall is not None:
        status = PASS if recall > 0.20 else WARN
        record("honesty", "recall at 5% budget", status, f"{recall:.3f}")

    cv = report.get("spatial_cv", [])
    if not cv:
        record("honesty", "spatial generalisation", WARN, "leave-one-region-out not run")
    else:
        mean_cv = sum(row["pr_auc"] for row in cv) / len(cv)
        if pr_auc and mean_cv < pr_auc * 0.6:
            record("honesty", "spatial generalisation", WARN,
                   f"held-out {mean_cv:.3f} vs in-sample {pr_auc:.3f} — "
                   "report both numbers")
        else:
            record("honesty", "spatial generalisation", PASS,
                   f"mean held-out pr_auc {mean_cv:.3f}")

    calibration = settings.MODELS_DIR / "calibration_v1.csv"
    if calibration.exists():
        curve = pd.read_csv(calibration)
        error = (curve["predicted"] - curve["observed"]).abs()
        weighted = float((error * curve["count"]).sum() / curve["count"].sum())
        status = PASS if weighted < 0.10 else WARN
        record("honesty", "calibration error", status, f"{weighted:.3f} mean |gap|")


def check_predictions() -> None:
    log.info("--- predictions ---")

    count = int(scalar("SELECT COUNT(*) FROM fact_risk_pred"))
    if count == 0:
        record("predictions", "risk predictions", WARN, "none scored yet")
        return
    record("predictions", "risk predictions", PASS, f"{count:,} rows")

    bad = int(scalar("""
        SELECT COUNT(*) FROM fact_risk_pred
        WHERE probability < 0 OR probability > 1
    """))
    record("predictions", "probability range", FAIL if bad else PASS,
           f"{bad:,} outside 0-1" if bad else "0-1")

    bands = db.read_sql("""
        SELECT risk_band, COUNT(*) AS n FROM fact_risk_pred
        GROUP BY risk_band ORDER BY n DESC
    """)
    critical = int(bands.loc[bands["risk_band"] == "critical", "n"].sum())
    share = critical / max(1, count)
    if share > 0.25:
        record("predictions", "critical share", WARN,
               f"{share:.1%} of cells critical — alerts this broad get ignored")
    else:
        record("predictions", "critical share", PASS, f"{share:.1%}")


def main() -> int:
    log.info("Slopewatch verification")
    log.info("=" * 72)

    check_completeness()
    check_integrity()
    check_leakage()
    check_metrics()
    check_predictions()

    failures = [check for check in results if check.status == FAIL]
    warnings = [check for check in results if check.status == WARN]

    log.info("=" * 72)
    log.info("%d checks: %d passed, %d warnings, %d failed",
             len(results), len(results) - len(failures) - len(warnings),
             len(warnings), len(failures))

    summary = pd.DataFrame([vars(check) for check in results])
    summary.to_csv(settings.PROCESSED_DIR / "verification.csv", index=False)

    if failures:
        log.error("--- failures ---")
        for check in failures:
            log.error("%s / %s: %s", check.group, check.name, check.detail)
        return 1

    if warnings:
        log.warning("passed with %d warnings — read them before presenting",
                    len(warnings))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log.error("verification could not run: %s", exc)
        raise
