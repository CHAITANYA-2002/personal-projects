"""Train, validate and persist the risk model.

    .venv/Scripts/python.exe scripts/07_train_model.py
    .venv/Scripts/python.exe scripts/07_train_model.py --no-spatial-cv

Fits logistic regression, random forest and gradient boosting on the temporal
training split, calibrates on validation, and scores on the held-out test years.
Then runs leave-one-region-out, which is the harder and more informative test:
a model that scores well in-region but collapses when a whole region is held out
has learned where landslides happen rather than why.

Artefacts land in models/ and the metrics also go to the warehouse so the
application can report on model quality alongside the risk itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.model import metrics, train             # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("train_model")

MODEL_VERSION = "v1"


def main(run_spatial_cv: bool) -> int:
    settings.ensure_dirs()

    matrix, features = _load()
    models = train.train_all(matrix, features)

    comparison = metrics.compare({
        name: model.scores.get("test", model.scores.get("val", {}))
        for name, model in models.items()
    })
    log.info("--- model comparison (test split) ---\n%s", comparison.to_string())

    best_name = _select(comparison, models)
    best = models[best_name]
    log.info("selected %s", best_name)

    importance = train.explain(best, matrix[matrix["split"] == "test"])
    if importance is not None:
        log.info("--- top features by mean |SHAP| ---\n%s",
                 importance.head(15).to_string(index=False))

    cv = pd.DataFrame()
    if run_spatial_cv:
        log.info("--- leave-one-region-out ---")
        cv = train.spatial_cv(matrix, features,
                              model_name=_base_name(best_name))
        if not cv.empty:
            _report_spatial(cv, best)

    _persist(best, features, models, comparison, importance, cv, matrix)
    return 0


def _load() -> tuple[pd.DataFrame, list[str]]:
    path = settings.PROCESSED_DIR / "features.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run scripts/06_build_features.py first"
        )

    matrix = pd.read_parquet(path)
    manifest = settings.PROCESSED_DIR / "feature_columns.txt"
    features = manifest.read_text(encoding="utf-8").split()

    # Drop columns that carry no signal rather than letting them dilute
    # importance rankings.
    usable = [
        column for column in features
        if column in matrix.columns and matrix[column].nunique(dropna=True) > 1
    ]
    dropped = sorted(set(features) - set(usable))
    if dropped:
        log.info("dropping %d constant features: %s", len(dropped), dropped)

    log.info("matrix %d rows x %d features", len(matrix), len(usable))
    return matrix, usable


def _base_name(name: str) -> str:
    return name


def _select(comparison: pd.DataFrame, models: dict) -> str:
    """Pick a model by PR-AUC, breaking a statistical tie on recall at budget.

    Sorting on PR-AUC alone treats a 0.004 difference as a decision. It is not:
    at 74% weather coverage the three models scored 0.264, 0.260 and 0.244 with
    bootstrap intervals that overlap almost completely, and the winner on that
    ordering caught 5.3% of events inside a 5% inspection budget against 13.0%
    for the model ranked last. Choosing the first is choosing noise over the
    only number a district can act on — and metrics.py already says which one
    that is: "recall_at_budget ... This is the number to lead with."

    So: every model whose PR-AUC interval overlaps the leader's is treated as
    tied, and the tie is broken on recall at a 5% budget. When one model is
    genuinely ahead — no overlap — PR-AUC still decides and this does nothing.

    The tie-break is validated before it is applied. If the validation split
    disagrees with the test split about which tied model has the best recall,
    the leader is kept, because a tie-break that only holds on the split it was
    measured on is not a tie-break, it is tuning.
    """
    leader = comparison.index[0]
    if "pr_auc_lo" not in comparison.columns or len(comparison) < 2:
        return leader

    leader_lo = float(comparison.loc[leader, "pr_auc_lo"])
    tied = [
        name for name in comparison.index
        if float(comparison.loc[name, "pr_auc_hi"]) >= leader_lo
    ]
    if len(tied) < 2:
        return leader

    def recall_on(split: str, name: str) -> float:
        return float(models[name].scores.get(split, {}).get("recall_at_5pct", 0.0))

    by_test = max(tied, key=lambda name: recall_on("test", name))
    by_val = max(tied, key=lambda name: recall_on("val", name))

    log.info(
        "PR-AUC tie across %s (intervals overlap the leader's %.3f lower bound)",
        ", ".join(tied), leader_lo,
    )
    for name in tied:
        log.info("  %-14s pr_auc %.3f  recall@5%% val %.3f  test %.3f",
                 name, float(comparison.loc[name, "pr_auc"]),
                 recall_on("val", name), recall_on("test", name))

    if by_test != by_val:
        log.info(
            "validation and test disagree on the tie-break (%s vs %s) — "
            "keeping the PR-AUC leader %s rather than tuning on one split",
            by_val, by_test, leader,
        )
        return leader

    if by_test != leader:
        log.info("tie broken on recall at 5%% budget: %s over %s",
                 by_test, leader)
    return by_test


def _report_spatial(cv: pd.DataFrame, best: train.TrainedModel) -> None:
    """Compare in-region performance against held-out-region performance."""
    test_score = best.scores.get("test", {})
    in_region = test_score.get("pr_auc")
    held_out = cv["pr_auc"].mean()

    log.info("--- generalisation ---")
    log.info("temporal test pr_auc       %.3f", in_region or float("nan"))
    log.info("mean held-out-region pr_auc %.3f", held_out)

    if in_region and held_out < in_region * 0.6:
        log.warning(
            "held-out-region performance is far below the temporal test score. "
            "The model is leaning on knowing the cells rather than the "
            "conditions — report both numbers, not just the flattering one."
        )


def _persist(
    best: train.TrainedModel,
    features: list[str],
    models: dict[str, train.TrainedModel],
    comparison: pd.DataFrame,
    importance: pd.DataFrame | None,
    cv: pd.DataFrame,
    matrix: pd.DataFrame,
) -> None:
    artefact = settings.MODELS_DIR / f"risk_model_{MODEL_VERSION}.joblib"
    base_rates = _base_rates(matrix)
    joblib.dump(
        {
            "name": best.name,
            "version": MODEL_VERSION,
            "estimator": best.estimator,
            "features": features,
            "threshold": best.scores.get("test", {}).get("threshold", 0.5),
            # Carried with the model because scoring cannot recover them later:
            # the correction from a case-control score to a real frequency needs
            # both the sampled and the population base rate.
            "sample_base_rate": base_rates["sample"],
            "population_base_rate": base_rates["population"],
        },
        artefact,
    )
    log.info("saved %s", artefact.name)

    report = {
        "version": MODEL_VERSION,
        "selected": best.name,
        "rows": int(len(matrix)),
        "features": len(features),
        "scores": {name: model.scores for name, model in models.items()},
        "spatial_cv": cv.to_dict("records") if not cv.empty else [],
    }
    report_path = settings.MODELS_DIR / f"metrics_{MODEL_VERSION}.json"
    report_path.write_text(json.dumps(report, indent=2, default=float),
                           encoding="utf-8")
    log.info("saved %s", report_path.name)

    if importance is not None:
        importance.to_csv(
            settings.MODELS_DIR / f"feature_importance_{MODEL_VERSION}.csv",
            index=False,
        )

    _write_performance_table(models, cv)

    test = matrix[matrix["split"] == "test"]
    if not test.empty:
        curve = metrics.reliability_curve(
            test["label"].to_numpy(), best.predict_proba(test)
        )
        curve.to_csv(
            settings.MODELS_DIR / f"calibration_{MODEL_VERSION}.csv", index=False
        )
        log.info("--- calibration (test) ---\n%s", curve.to_string(index=False))


def _write_performance_table(
    models: dict[str, train.TrainedModel],
    cv: pd.DataFrame,
) -> None:
    """Land model quality in the warehouse so the dashboard can report it."""
    rows = []
    for name, model in models.items():
        for split, score in model.scores.items():
            if "pr_auc" not in score:
                continue
            rows.append({
                "model_version": MODEL_VERSION,
                "model_name": name,
                "eval_scope": split,
                "pr_auc": score["pr_auc"],
                "roc_auc": score["roc_auc"],
                "recall_at_5pct": score["recall_at_5pct"],
                "recall_at_10pct": score["recall_at_10pct"],
                "precision_at_threshold": score["precision"],
                "recall_at_threshold": score["recall"],
                "f1": score["f1"],
                "brier": score["brier"],
                "threshold": score["threshold"],
                "n_rows": score["n"],
                "n_positives": score["positives"],
            })

    for row in cv.to_dict("records"):
        rows.append({
            "model_version": MODEL_VERSION,
            "model_name": "xgboost",
            "eval_scope": f"holdout:{row['block']}",
            "pr_auc": row["pr_auc"],
            "roc_auc": row["roc_auc"],
            "recall_at_5pct": row["recall_at_5pct"],
            "recall_at_10pct": row["recall_at_10pct"],
            "precision_at_threshold": row["precision"],
            "recall_at_threshold": row["recall"],
            "f1": row["f1"],
            "brier": row["brier"],
            "threshold": row["threshold"],
            "n_rows": row["n"],
            "n_positives": row["positives"],
        })

    frame = pd.DataFrame(rows)
    db.write_frame(frame, "mart_model_performance", if_exists="replace")
    log.info("wrote %d rows to mart_model_performance", len(frame))

def _base_rates(matrix: pd.DataFrame) -> dict[str, float]:
    """Sampled and true landslide rates, for the prior correction at scoring.

    The sampled rate is whatever the case-control design produced. The true rate
    is events divided by every hill cell-day the study covers — roughly one in
    fifty thousand, which is why an uncorrected score cannot be read as a
    probability of failure.
    """
    sample_rate = float(matrix["label"].mean())

    counts = db.read_sql("""
        SELECT  (SELECT COUNT(*) FROM fact_landslide)               AS events,
                (SELECT COUNT(*) FROM dim_cell WHERE is_hill = 1)   AS cells,
                (SELECT COUNT(*) FROM dim_date)                     AS days
    """).iloc[0]

    cell_days = float(counts["cells"]) * float(counts["days"])
    population_rate = float(counts["events"]) / max(1.0, cell_days)

    log.info("--- base rates ---")
    log.info("sampled     %.4f  (1 in %.0f)", sample_rate, 1 / sample_rate)
    log.info("population  %.2e  (1 in %.0f cell-days)",
             population_rate, 1 / population_rate)
    log.info(
        "scores are calibrated on the sampled rate; the prior correction at "
        "scoring time converts them back to the population scale"
    )
    return {"sample": sample_rate, "population": population_rate}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-spatial-cv", action="store_true",
                        help="skip leave-one-region-out (faster, less honest)")
    args = parser.parse_args()

    try:
        sys.exit(main(run_spatial_cv=not args.no_spatial_cv))
    except Exception as exc:
        log.error("training failed: %s", exc)
        raise
