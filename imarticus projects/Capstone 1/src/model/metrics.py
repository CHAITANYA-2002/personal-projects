"""Evaluation metrics for a rare-event warning system.

Accuracy is deliberately absent. At a 1:15 sampling ratio a model that predicts
"safe" everywhere scores 94%, and on the real grid it would score 99.99% — the
number is not merely uninformative, it actively hides failure.

What replaces it:

  pr_auc              the honest headline under heavy class imbalance
  recall_at_budget    the operational question. If a district can inspect its
                      top 5% of cells today, what share of real landslides did
                      it catch? This is the number to lead with.
  brier / calibration whether a stated 80% means 80%. An officer acting on the
                      number needs it to be true, not merely well-ranked.
  lead_time           how much warning the system actually buys
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)

log = logging.getLogger(__name__)

# Alert budgets a district could plausibly act on in one day.
ALERT_BUDGETS = (0.01, 0.05, 0.10, 0.20)

# False negatives are far costlier than false positives here: a missed landslide
# can cost lives, a false alarm costs an inspection vehicle. The operating
# threshold is chosen to reflect that rather than defaulting to 0.5.
FALSE_NEGATIVE_COST = 20.0
FALSE_POSITIVE_COST = 1.0


def evaluate(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float | None = None,
) -> dict[str, float]:
    """Full metric set for one set of predictions."""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)

    if y_true.sum() == 0:
        log.warning("no positives present — most metrics are undefined")
        return {"positives": 0, "n": len(y_true)}

    threshold = threshold if threshold is not None else cost_optimal_threshold(
        y_true, y_prob
    )

    predicted = (y_prob >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, predicted, labels=[0, 1]).ravel()

    results = {
        "n": int(len(y_true)),
        "positives": int(y_true.sum()),
        "base_rate": float(y_true.mean()),
        "pr_auc": float(average_precision_score(y_true, y_prob)),
        "roc_auc": float(roc_auc_score(y_true, y_prob)),
        "brier": float(brier_score_loss(y_true, y_prob)),
        "threshold": float(threshold),
        "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
        "precision": float(tp / (tp + fp)) if tp + fp else 0.0,
        "recall": float(tp / (tp + fn)) if tp + fn else 0.0,
    }
    denominator = results["precision"] + results["recall"]
    results["f1"] = (
        2 * results["precision"] * results["recall"] / denominator
        if denominator else 0.0
    )

    for budget in ALERT_BUDGETS:
        results[f"recall_at_{int(budget * 100)}pct"] = recall_at_budget(
            y_true, y_prob, budget
        )

    lower, upper = pr_auc_interval(y_true, y_prob)
    results["pr_auc_lo"] = lower
    results["pr_auc_hi"] = upper

    return results


BOOTSTRAP_SAMPLES = 400
BOOTSTRAP_SEED = 42


def pr_auc_interval(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    samples: int = BOOTSTRAP_SAMPLES,
) -> tuple[float, float]:
    """Bootstrap 95% interval for PR-AUC.

    Reported because a point estimate on a hundred positives invites a false
    reading. Two runs scoring 0.29 and 0.33 look like an improvement and are
    usually the same model on a slightly different test set — especially here,
    where the test split grows as the weather backfill lands, so successive
    scores are not measured on the same data and are not directly comparable.
    The interval makes that visible instead of leaving it to be discovered.
    """
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)

    if y_true.sum() < 5:
        return (float("nan"), float("nan"))

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    scores: list[float] = []
    indices = np.arange(len(y_true))

    for _ in range(samples):
        picked = rng.choice(indices, size=len(indices), replace=True)
        if y_true[picked].sum() < 2:
            continue
        scores.append(average_precision_score(y_true[picked], y_prob[picked]))

    if not scores:
        return (float("nan"), float("nan"))

    return (
        float(np.percentile(scores, 2.5)),
        float(np.percentile(scores, 97.5)),
    )


def recall_at_budget(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    budget: float,
) -> float:
    """Share of real events inside the highest-risk `budget` fraction of cells."""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)

    positives = y_true.sum()
    if positives == 0:
        return 0.0

    take = max(1, int(round(len(y_prob) * budget)))
    top = np.argsort(-y_prob)[:take]
    return float(y_true[top].sum() / positives)


def cost_optimal_threshold(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    fn_cost: float = FALSE_NEGATIVE_COST,
    fp_cost: float = FALSE_POSITIVE_COST,
) -> float:
    """Threshold minimising expected cost, stated rather than assumed at 0.5."""
    precision, recall, thresholds = precision_recall_curve(y_true, y_prob)
    if thresholds.size == 0:
        return 0.5

    positives = int(np.asarray(y_true).sum())
    negatives = len(y_true) - positives

    best_threshold, best_cost = 0.5, float("inf")
    for index, threshold in enumerate(thresholds):
        r = recall[index]
        p = precision[index]
        if p <= 0:
            continue
        true_positives = r * positives
        false_negatives = positives - true_positives
        false_positives = true_positives * (1 - p) / p
        cost = fn_cost * false_negatives + fp_cost * false_positives
        if cost < best_cost:
            best_cost, best_threshold = cost, float(threshold)

    return best_threshold


def reliability_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    bins: int = 10,
) -> pd.DataFrame:
    """Predicted probability against observed frequency, for the calibration plot."""
    frame = pd.DataFrame({"y": np.asarray(y_true), "p": np.asarray(y_prob)})
    frame["bin"] = pd.cut(frame["p"], bins=np.linspace(0, 1, bins + 1),
                          include_lowest=True)

    curve = (
        frame.groupby("bin", observed=True)
        .agg(predicted=("p", "mean"), observed=("y", "mean"), count=("y", "size"))
        .reset_index()
    )
    curve["bin"] = curve["bin"].astype(str)
    return curve


def compare(results: dict[str, dict[str, float]]) -> pd.DataFrame:
    """Side-by-side model comparison, sorted by the metric that matters."""
    frame = pd.DataFrame(results).T
    keep = [
        "pr_auc", "pr_auc_lo", "pr_auc_hi", "roc_auc",
        "recall_at_5pct", "recall_at_10pct",
        "precision", "recall", "f1", "brier", "threshold",
    ]
    available = [column for column in keep if column in frame.columns]
    return frame[available].sort_values("pr_auc", ascending=False).round(4)
