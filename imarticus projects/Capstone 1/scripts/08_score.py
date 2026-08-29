"""Score the forecast window and write fact_risk_pred.

    .venv/Scripts/python.exe scripts/08_score.py
    .venv/Scripts/python.exe scripts/08_score.py --cells 500 --forecast-days 7

This is the job a scheduler would run daily. It pulls the last 60 days plus the
16-day forecast for each cell, rebuilds features through the same module that
trained the model, scores, bands, prioritises and stores.

The feature-parity assertion near the end is not ceremony. Training/serving skew
is the standard way a system like this rots, and it fails silently — the model
keeps returning confident numbers computed from columns that no longer mean what
they meant in training.

Scoring every hill cell daily would exceed the free API allowance, so the cell
set is bounded and the bound is logged rather than hidden.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.data import openmeteo as om             # noqa: E402
from src.features import build as features       # noqa: E402
from src.model import scoring, train             # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("score")

MODEL_VERSION = "v1"


def main(n_cells: int, forecast_days: int, dry_run: bool) -> int:
    settings.ensure_dirs()

    bundle = _load_model()
    cells = _cells_to_score(n_cells)
    log.info("scoring %d cells over %d forecast days", len(cells), forecast_days)

    fetcher = om.WeatherFetcher()
    weather = fetcher.fetch_forecast(
        cells, past_days=60, forecast_days=forecast_days
    )
    if weather.empty:
        log.error("no weather returned — nothing to score")
        return 1

    matrix = _build_matrix(weather, cells, forecast_days)
    _assert_feature_parity(matrix, bundle["features"])

    probabilities = bundle["estimator"].predict_proba(
        matrix[bundle["features"]]
    )[:, 1]

    scored = _assemble(matrix, probabilities, bundle)
    _ensure_dates(scored["date_id"])
    _report(scored)

    if dry_run:
        log.info("--dry-run given, not writing to the warehouse")
        return 0

    _persist(scored)
    return 0


def _ensure_dates(date_ids) -> None:
    """Extend dim_date to cover the forecast window.

    The date dimension is built for the training years, but scoring runs into
    the future. Without this the predictions land in fact_risk_pred and then
    vanish from every report, because the marts join through dim_date and the
    join silently drops them — a dashboard showing nothing looks like a quiet
    day rather than a missing dimension row.
    """
    from src.data import grid

    wanted = set(int(value) for value in pd.Series(date_ids).unique())
    known = set(
        db.read_sql("SELECT date_id FROM dim_date")["date_id"].astype(int)
    )
    missing = sorted(wanted - known)
    if not missing:
        return

    frame = grid.build_date_frame(
        grid.id_to_date(missing[0]), grid.id_to_date(missing[-1])
    )
    frame = frame[frame["date_id"].isin(missing)]
    db.write_frame(frame, "dim_date")
    log.info("extended dim_date with %d forecast days", len(frame))


def _load_model() -> dict:
    path = settings.MODELS_DIR / f"risk_model_{MODEL_VERSION}.joblib"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run scripts/07_train_model.py first"
        )
    bundle = joblib.load(path)
    log.info("loaded %s (%s, %d features)",
             path.name, bundle["name"], len(bundle["features"]))
    return bundle


def _cells_to_score(n_cells: int) -> pd.DataFrame:
    """Cells worth the API budget: those with history, then the steepest.

    A production deployment would score the whole hill mask. On the free tier
    that is roughly 80,000 calls a day against a 10,000 allowance, so the set is
    bounded here and the bound is stated in the log.
    """
    cells = db.read_sql(f"""
        SELECT      c.cell_id, c.lat_c, c.lon_c, c.slope_mean,
                    COUNT(f.event_id)                       AS past_events
        FROM        dim_cell c
        LEFT JOIN   fact_landslide f ON f.cell_id = c.cell_id
        WHERE       c.is_hill = 1
        GROUP BY    c.cell_id, c.lat_c, c.lon_c, c.slope_mean
        ORDER BY    past_events DESC, c.slope_mean DESC
        LIMIT       {int(n_cells)}
    """)
    if cells.empty:
        raise RuntimeError(
            "no hill cells available — run scripts/03_build_terrain.py first"
        )

    total_hill = db.read_sql(
        "SELECT COUNT(*) AS n FROM dim_cell WHERE is_hill = 1"
    )["n"].iloc[0]
    log.info(
        "selected %d of %d hill cells (%.1f%% of the mask) — the rest are "
        "outside today's API budget, not outside the model",
        len(cells), total_hill, 100 * len(cells) / max(1, int(total_hill)),
    )
    return cells


def _build_matrix(
    weather: pd.DataFrame,
    cells: pd.DataFrame,
    forecast_days: int,
) -> pd.DataFrame:
    """Features for every cell on every forecast day."""
    today = int(date.today().strftime("%Y%m%d"))
    horizon = weather[weather["date_id"] >= today]["date_id"].unique()
    horizon = np.sort(horizon)[:forecast_days]

    targets = pd.DataFrame(
        [
            {"cell_id": int(cell_id), "date_id": int(date_id)}
            for cell_id in cells["cell_id"]
            for date_id in horizon
        ]
    )
    log.info("scoring frame: %d cell-days", len(targets))

    terrain = db.read_sql(f"""
        SELECT cell_id, {', '.join(features.STATIC_COLUMNS)}
        FROM   dim_cell
    """)
    events = db.read_sql("SELECT cell_id, date_id FROM fact_landslide")

    return features.build_features(targets, weather, terrain, events)


def _assert_feature_parity(matrix: pd.DataFrame, expected: list[str]) -> None:
    """Fail loudly if serving features drifted from training features."""
    missing = [column for column in expected if column not in matrix.columns]
    if missing:
        raise RuntimeError(
            f"feature parity broken — training used {len(expected)} features, "
            f"{len(missing)} are absent at scoring time: {missing[:10]}"
        )
    log.info("feature parity: all %d training features present", len(expected))


def _assemble(
    matrix: pd.DataFrame,
    probabilities: np.ndarray,
    bundle: dict,
) -> pd.DataFrame:
    scored = matrix[["cell_id", "date_id"]].copy()
    scored["model_version"] = MODEL_VERSION
    scored["probability"] = np.round(probabilities, 5)
    scored["risk_band"] = scoring.assign_bands(
        scored["probability"], bundle.get("sample_base_rate")
    ).to_numpy()

    # The stored score is calibrated against the case-control sample, where
    # positives are roughly one row in six. Reporting it as a chance of failure
    # would overstate the real frequency by four orders of magnitude, so the
    # corrected value is stored alongside and the two are never conflated.
    sample_rate = bundle.get("sample_base_rate")
    population_rate = bundle.get("population_base_rate")
    if sample_rate and population_rate:
        scored["absolute_probability"] = scoring.absolute_probability(
            scored["probability"], sample_rate, population_rate
        ).round(10).to_numpy()
        log.info(
            "prior correction applied: sampled base rate %.3f, population "
            "%.2e — relative score %.3f maps to an absolute %.2e",
            sample_rate, population_rate,
            float(scored["probability"].max()),
            float(scored["absolute_probability"].max()),
        )
    else:
        scored["absolute_probability"] = None
        log.warning(
            "model bundle carries no base rates — retrain so scores can be "
            "corrected to a real frequency"
        )

    exposure = db.read_sql("""
        SELECT cell_id, road_km_total, settlements, est_population,
               schools, health_facilities
        FROM   fact_exposure
    """)
    if exposure.empty:
        log.warning(
            "fact_exposure is empty — priority falls back to probability alone. "
            "Run the exposure build to rank by who is actually downhill."
        )
        scored["priority_score"] = scored["probability"]
    else:
        exposure = exposure.set_index("cell_id")
        weights = scoring.exposure_score(exposure)
        mapped = scored["cell_id"].map(weights).fillna(0)
        mapped.index = scored.index
        scored["priority_score"] = scoring.priority(scored["probability"], mapped)

    drivers = _drivers(matrix, bundle)
    for index in range(3):
        scored[f"driver_{index + 1}"] = [row[index] for row in drivers]

    return scored


def _drivers(matrix: pd.DataFrame, bundle: dict) -> list[list[str]]:
    """Per-cell SHAP attributions, so each score carries its own explanation.

    Reuses the training module's explainer selection rather than assuming a tree
    model — the selected model may well be the logistic regression, and picking
    the wrong explainer here would silently strip every prediction of its reason.
    """
    empty = [[None, None, None] for _ in range(len(matrix))]

    model = train.TrainedModel(
        name=bundle["name"],
        estimator=bundle["estimator"],
        features=bundle["features"],
    )
    inner = train._unwrap(bundle["estimator"])

    values = train.shap_values(inner, model, matrix)
    if values is None:
        log.warning("driver attribution unavailable — predictions carry no reason")
        return empty

    return scoring.top_drivers(values, bundle["features"])


def _report(scored: pd.DataFrame) -> None:
    log.info("--- risk bands across the forecast window ---")
    for row in scoring.summarise(scored).itertuples(index=False):
        log.info("%-10s %6d", row.risk_band, row.cells)

    top = scored.nlargest(10, "priority_score")
    detail = db.read_sql("SELECT cell_id, lat_c, lon_c, state_name FROM dim_cell")
    top = top.merge(detail, on="cell_id", how="left")

    log.info("--- highest priority cells ---")
    for row in top.itertuples(index=False):
        log.info(
            "%8s  %s  p=%.3f  %-9s  %.2fN %.2fE  %s",
            row.cell_id, row.date_id, row.probability, row.risk_band,
            row.lat_c, row.lon_c, row.driver_1 or "",
        )


def _persist(scored: pd.DataFrame) -> None:
    columns = ["cell_id", "date_id", "model_version", "probability",
               "absolute_probability", "risk_band", "priority_score",
               "driver_1", "driver_2", "driver_3"]

    with db.get_engine().connect() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "DELETE FROM fact_risk_pred WHERE model_version = :version"
        ), {"version": MODEL_VERSION})
        conn.commit()

    db.write_frame(scored[columns], "fact_risk_pred", chunksize=5_000)
    log.info("wrote %d predictions", len(scored))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", type=int, default=800,
                        help="how many hill cells to score (API budget bound)")
    parser.add_argument("--forecast-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true",
                        help="score and report without writing to the warehouse")
    args = parser.parse_args()

    try:
        sys.exit(main(n_cells=args.cells, forecast_days=args.forecast_days,
                      dry_run=args.dry_run))
    except Exception as exc:
        log.error("scoring failed: %s", exc)
        raise
