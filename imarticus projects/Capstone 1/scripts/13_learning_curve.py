"""How much of the score is missing because the backfill is unfinished?

    .venv/Scripts/python.exe scripts/13_learning_curve.py
    .venv/Scripts/python.exe scripts/13_learning_curve.py --model xgboost --repeats 12

"The dataset is small" is an excuse until it is measured, at which point it
becomes a finding. This subsamples the training split at fixed fractions, holds
the test split fixed, and reports PR-AUC at each size. The shape of that curve
answers a question the point estimate cannot: whether finishing the weather
backfill would move the model, or whether the ceiling is somewhere else.

Read the increments, not the levels. A curve still climbing steeply at 100% of
the available data says fetch more. A curve flattening says the limit is the
predictors — here, daily rainfall sums on an 11 km grid against a trigger that
is sub-daily and sub-kilometre — and more rows of the same thing will not fix
it.

Deliberately uncalibrated. train_all applies Platt scaling on the validation
split, which costs a little point PR-AUC to buy reliability; including it here
would mix two effects in one number. Levels therefore sit slightly above what
07_train_model.py reports, and only the trend is being read.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src.logging_setup import configure          # noqa: E402
from src.model import metrics, train             # noqa: E402

log = configure("learning_curve")

FIGURES = settings.PROJECT_ROOT / "reports" / "figures"
FRACTIONS = (0.25, 0.40, 0.55, 0.70, 0.85, 1.00)


def main(model_name: str, repeats: int, fractions: tuple[float, ...]) -> int:
    warnings.filterwarnings("ignore")

    frame, features = _load()
    train_split = frame[frame["split"] == "train"]
    test_split = frame[frame["split"] == "test"]

    if train_split.empty or test_split.empty:
        raise RuntimeError("need both a train and a test split — rerun 06")

    y_test = test_split["label"].to_numpy()
    log.info("train %d rows (%d positives) | test %d rows (%d positives) | "
             "%d features",
             len(train_split), int(train_split["label"].sum()),
             len(test_split), int(y_test.sum()), len(features))

    rows = [
        _one_point(train_split, test_split, features, model_name, frac, repeats)
        for frac in fractions
    ]
    curve = pd.DataFrame(rows)

    _report(curve, model_name, float(y_test.mean()))
    _persist(curve, model_name)
    return 0


def _load() -> tuple[pd.DataFrame, list[str]]:
    matrix = settings.PROCESSED_DIR / "features.parquet"
    manifest = settings.PROCESSED_DIR / "feature_columns.txt"
    if not matrix.exists():
        raise FileNotFoundError(
            f"{matrix} is missing — run scripts/06_build_features.py first"
        )
    features = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return pd.read_parquet(matrix), features


def _one_point(
    train_split: pd.DataFrame,
    test_split: pd.DataFrame,
    features: list[str],
    model_name: str,
    fraction: float,
    repeats: int,
) -> dict:
    """PR-AUC at one training size, averaged over independent subsamples.

    Each class is subsampled separately so the positive rate stays put. Letting
    it drift would confound size with class balance, and PR-AUC moves with the
    base rate.
    """
    positives = train_split[train_split["label"] == 1]
    negatives = train_split[train_split["label"] == 0]
    y_test = test_split["label"].to_numpy()

    scores: list[float] = []
    n_rows = n_positives = 0

    for seed in range(repeats):
        if fraction >= 1.0:
            subset = train_split
        else:
            subset = pd.concat([
                positives.sample(frac=fraction, random_state=seed),
                negatives.sample(frac=fraction, random_state=seed),
            ])

        n_positives = int(subset["label"].sum())
        n_rows = len(subset)
        if n_positives < 10:
            log.warning("fraction %.2f leaves only %d positives — skipping",
                        fraction, n_positives)
            return {"fraction": fraction, "rows": n_rows,
                    "positives": n_positives, "events_per_variable": np.nan,
                    "pr_auc": np.nan, "pr_auc_sd": np.nan}

        estimator = train.build_estimators(
            (n_rows - n_positives) / n_positives, n_positives
        )[model_name]
        estimator.fit(subset[features], subset["label"])
        probabilities = estimator.predict_proba(test_split[features])[:, 1]
        scores.append(metrics.evaluate(y_test, probabilities)["pr_auc"])

        # A full-size fit is deterministic; repeating it measures nothing.
        if fraction >= 1.0:
            break

    return {
        "fraction": fraction,
        "rows": n_rows,
        "positives": n_positives,
        "events_per_variable": round(n_positives / len(features), 1),
        "pr_auc": round(float(np.mean(scores)), 4),
        "pr_auc_sd": round(float(np.std(scores)), 4),
    }


def _report(curve: pd.DataFrame, model_name: str, base_rate: float) -> None:
    log.info("--- learning curve (%s, uncalibrated) ---", model_name)
    log.info("%6s %7s %6s %6s %16s", "frac", "rows", "pos", "EPV", "test pr_auc")
    for row in curve.itertuples(index=False):
        log.info("%6.2f %7d %6d %6.1f   %.3f +- %.3f",
                 row.fraction, row.rows, row.positives,
                 row.events_per_variable, row.pr_auc, row.pr_auc_sd)

    log.info("test base rate %.3f", base_rate)

    usable = curve.dropna(subset=["pr_auc"])
    if len(usable) < 3:
        return

    # The last third of the curve is what extrapolates. Early gains are the
    # model escaping a sample too small to fit forty features at all, and they
    # say nothing about what the next four-fold increase would buy.
    tail = usable.tail(3)
    gained = float(tail["pr_auc"].iloc[-1] - tail["pr_auc"].iloc[0])
    grown = float(tail["rows"].iloc[-1] / tail["rows"].iloc[0])
    log.info("--- reading ---")
    log.info("last %.1fx of training data bought %+.3f pr_auc", grown, gained)
    if gained < 0.02:
        log.info(
            "flat: the ceiling is predictor resolution, not sample size. "
            "Daily rainfall sums on an %.0f km grid cannot see a three-hour "
            "cloudburst, and more rows of the same feature will not add what "
            "was never measured.",
            settings.GRID_DEG * 111,
        )
    else:
        log.info(
            "still climbing: finishing the weather backfill is worth doing "
            "for the score as well as for the sample size"
        )


def _persist(curve: pd.DataFrame, model_name: str) -> None:
    out = settings.PROJECT_ROOT / "models" / f"learning_curve_{model_name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    curve.to_csv(out, index=False)
    log.info("wrote %s", out.name)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:                                   # pragma: no cover
        log.warning("matplotlib unavailable — skipping the figure")
        return

    usable = curve.dropna(subset=["pr_auc"])
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.errorbar(usable["rows"], usable["pr_auc"], yerr=usable["pr_auc_sd"],
                marker="o", capsize=4, linewidth=1.6, color="#1f4e79")
    ax.set_xlabel("training rows")
    ax.set_ylabel("test PR-AUC")
    ax.set_title(f"Learning curve — {model_name}, test split held fixed")
    ax.grid(alpha=0.3)

    for row in usable.itertuples(index=False):
        ax.annotate(f"{row.positives} pos", (row.rows, row.pr_auc),
                    textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8, color="#555")

    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / f"07_learning_curve_{model_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("wrote %s", path.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="logistic",
                        choices=("logistic", "random_forest", "xgboost"))
    parser.add_argument("--repeats", type=int, default=8,
                        help="subsamples averaged per point")
    parser.add_argument("--fractions", type=float, nargs="+", default=None,
                        help="training fractions to evaluate")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(
        args.model, args.repeats,
        tuple(args.fractions) if args.fractions else FRACTIONS,
    ))
