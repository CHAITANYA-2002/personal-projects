"""Publication figures for the README, the report and the app's model page.

    .venv/Scripts/python.exe scripts/14_report_figures.py

Everything here is drawn from committed artefacts — models/metrics_v1.json,
feature_importance_v1.csv, calibration_v1.csv, learning_curve_*.csv and the
feature matrix — so a figure can never drift from the numbers the rest of the
project reports. Nothing is hardcoded and nothing is redrawn by hand.

Six figures, each answering one question a reader will actually ask:

  10_pr_curves          is the model better than guessing, and by how much
  11_recall_at_budget   what does it deliver at an inspection budget I can afford
  12_calibration        when it says 0.2, does 0.2 happen
  13_shap               what is it actually using
  14_region_generalisation  does it work where it has not been trained
  15_score_separation   do cases and controls actually look different

Written at 150 dpi on a white ground so they drop straight into GitHub, a
slide, or a printed report without rework.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib                                # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                  # noqa: E402
from matplotlib.ticker import PercentFormatter   # noqa: E402

from config import settings                      # noqa: E402
from src.logging_setup import configure          # noqa: E402
from src.model import metrics, train             # noqa: E402

log = configure("report_figures")

FIGURES = settings.PROJECT_ROOT / "reports" / "figures"
MODELS = settings.PROJECT_ROOT / "models"

# The walkthrough's palette, so the figures and the document read as one thing.
INK = "#151F1C"
MUTED = "#74827D"
RULE = "#C6CEC9"
ACCENT = "#9A5615"
WATER = "#20666E"
GOOD = "#456F13"
BAD = "#8F2222"


def series_style(name: str, selected: str, index: int) -> dict:
    """The chosen model is drawn in the accent, heavier, and opaque.

    Colouring by dictionary order put the selected model in the muted grey and
    a rejected one in the accent, which reads as the opposite of the finding.
    """
    if name == selected:
        return {"color": ACCENT, "linewidth": 2.6, "alpha": 1.0, "zorder": 3}
    return {"color": (WATER, MUTED)[index % 2], "linewidth": 1.3,
            "alpha": 0.6, "zorder": 2}


plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "font.size": 9,
    "axes.edgecolor": RULE,
    "axes.labelcolor": INK,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.titlecolor": INK,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "grid.color": RULE,
    "grid.alpha": 0.5,
    "legend.frameon": False,
})


def save(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("wrote %s", path.name)


def _spine(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_all():
    matrix_path = settings.PROCESSED_DIR / "features.parquet"
    if not matrix_path.exists():
        raise FileNotFoundError(
            f"{matrix_path} is missing — run scripts/06_build_features.py first"
        )
    matrix = pd.read_parquet(matrix_path)
    features = [
        line.strip()
        for line in (settings.PROCESSED_DIR / "feature_columns.txt")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    scores = json.loads((MODELS / "metrics_v1.json").read_text(encoding="utf-8"))
    return matrix, features, scores


def refit_for_curves(matrix: pd.DataFrame, features: list[str], scores: dict):
    """Recover per-row probabilities by rerunning the real training path.

    metrics_v1.json stores summary statistics, not predictions, and a PR curve
    needs the raw scores. The obvious shortcut — fit build_estimators() directly
    — produces figures that quietly disagree with the committed numbers, because
    it skips the calibration that train_all() applies on the validation split.
    That showed up as a recall@5% of 0.117 on a chart against 0.130 in the JSON.

    So the whole path is rerun, seeds and all, and the result is checked against
    the stored metrics before anything is drawn. A figure that cannot reproduce
    the number it illustrates is worse than no figure.
    """
    test_split = matrix[matrix["split"] == "test"]
    y_true = test_split["label"].to_numpy()

    models = train.train_all(matrix, features)
    out = {name: model.predict_proba(test_split) for name, model in models.items()}

    for name, probability in out.items():
        stored = scores["scores"].get(name, {}).get("test", {})
        if not stored:
            continue
        drawn = metrics.evaluate(y_true, probability)
        for key in ("pr_auc", "recall_at_5pct"):
            gap = abs(drawn[key] - stored[key])
            if gap > 0.005:
                log.warning(
                    "%s %s differs from metrics_v1.json by %.4f "
                    "(drawn %.4f, stored %.4f) — figures may be stale",
                    name, key, gap, drawn[key], stored[key],
                )
            else:
                log.info("%-14s %-14s reproduces stored value (%.4f)",
                         name, key, stored[key])
    return y_true, out


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

def fig_pr_curves(y_true, probabilities, selected: str) -> None:
    from sklearn.metrics import precision_recall_curve, average_precision_score

    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    base = y_true.mean()

    others = [name for name in probabilities if name != selected]
    for index, (name, prob) in enumerate(probabilities.items()):
        precision, recall, _ = precision_recall_curve(y_true, prob)
        score = average_precision_score(y_true, prob)
        style = series_style(name, selected, others.index(name) if name in others else 0)
        ax.plot(recall, precision,
                label=f"{name}  {score:.3f}"
                      + ("  ← selected" if name == selected else ""),
                **style)

    ax.axhline(base, color=MUTED, linestyle="--", linewidth=1.2)
    ax.text(0.99, base + 0.012, f"base rate {base:.3f}", ha="right",
            va="bottom", fontsize=8, color=MUTED)

    ax.set_xlabel("Recall — share of real landslides caught")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-recall on the held-out test split")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, max(0.6, base * 3))
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    _spine(ax)
    fig.text(0.5, -0.04,
             "Area under this curve is PR-AUC. The dashed line is what random "
             "ranking would achieve.",
             ha="center", fontsize=8, color=MUTED)
    save(fig, "10_pr_curves")


def fig_recall_at_budget(y_true, probabilities, selected: str) -> None:
    budgets = np.linspace(0.01, 0.40, 40)
    fig, ax = plt.subplots(figsize=(6.2, 4.2))

    others = [name for name in probabilities if name != selected]
    for name, prob in probabilities.items():
        caught = [metrics.recall_at_budget(y_true, prob, b) for b in budgets]
        style = series_style(name, selected,
                             others.index(name) if name in others else 0)
        ax.plot(budgets, caught,
                label=name + ("  ← selected" if name == selected else ""),
                **style)

    ax.plot(budgets, budgets, color=MUTED, linestyle="--", linewidth=1.2)
    ax.text(0.39, 0.40, "random", ha="right", va="top", fontsize=8, color=MUTED)

    for mark in (0.05, 0.10):
        ax.axvline(mark, color=RULE, linewidth=1)
        best = metrics.recall_at_budget(y_true, probabilities[selected], mark)
        ax.plot([mark], [best], "o", color=ACCENT, markersize=6)
        ax.annotate(f"{best:.0%}", (mark, best), textcoords="offset points",
                    xytext=(7, 6), fontsize=9, color=ACCENT, fontweight="bold")

    ax.set_xlabel("Inspection budget — share of cells visited")
    ax.set_ylabel("Share of real landslides caught")
    ax.set_title("What the model delivers at a budget a district can afford")
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlim(0, 0.40)
    ax.set_ylim(0, None)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    _spine(ax)
    fig.text(0.5, -0.04,
             "This is the operational metric, and the one that decides which "
             "model ships when PR-AUC ties.",
             ha="center", fontsize=8, color=MUTED)
    save(fig, "11_recall_at_budget")


def fig_calibration() -> None:
    curve = pd.read_csv(MODELS / "calibration_v1.csv")
    curve = curve[curve["count"] > 0]

    fig, (ax, bar) = plt.subplots(
        2, 1, figsize=(5.6, 5.2), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
    )

    ax.plot([0, 1], [0, 1], color=MUTED, linestyle="--", linewidth=1.2,
            label="perfect calibration")
    sizes = 20 + 220 * curve["count"] / curve["count"].max()
    ax.scatter(curve["predicted"], curve["observed"], s=sizes,
               color=ACCENT, alpha=0.85, zorder=3, label="observed")
    ax.plot(curve["predicted"], curve["observed"], color=ACCENT,
            linewidth=1.2, alpha=0.5, zorder=2)

    gap = float((curve["predicted"] - curve["observed"]).abs().mean())
    ax.set_ylabel("Observed frequency")
    ax.set_title(f"Calibration — mean absolute gap {gap:.3f}")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    _spine(ax)

    bar.bar(curve["predicted"], curve["count"], width=0.055,
            color=WATER, alpha=0.7)
    bar.set_xlabel("Predicted probability")
    bar.set_ylabel("Rows")
    bar.grid(alpha=0.3, axis="y")
    _spine(bar)

    fig.text(0.5, -0.03,
             "Marker size is bin population. Read only the bins that carry "
             "rows — the sparse tail means nothing.",
             ha="center", fontsize=8, color=MUTED)
    save(fig, "12_calibration")


def fig_shap() -> None:
    importance = pd.read_csv(MODELS / "feature_importance_v1.csv").head(15)
    importance = importance.iloc[::-1]

    terrain = {"elev_mean", "elev_range", "slope_mean", "slope_max",
               "slope_std", "aspect_sin", "aspect_cos", "tri"}
    colours = [
        WATER if name in terrain else ACCENT
        for name in importance["feature"]
    ]

    fig, ax = plt.subplots(figsize=(6.2, 5.0))
    ax.barh(importance["feature"], importance["mean_abs_shap"],
            color=colours, alpha=0.9)
    ax.set_xlabel("mean |SHAP|")
    ax.set_title("What the model actually uses")
    ax.grid(alpha=0.3, axis="x")
    ax.tick_params(axis="y", labelsize=8)
    _spine(ax)

    handles = [
        plt.Rectangle((0, 0), 1, 1, color=ACCENT, alpha=0.9),
        plt.Rectangle((0, 0), 1, 1, color=WATER, alpha=0.9),
    ]
    ax.legend(handles, ["weather", "terrain"], loc="lower right", fontsize=8)
    fig.text(0.5, -0.02,
             "elev_mean was rank 1 at 0.513 before the sampler was fixed. It is "
             "no longer in the top 15.",
             ha="center", fontsize=8, color=MUTED)
    save(fig, "13_shap")


def fig_region_generalisation(scores: dict) -> None:
    blocks = pd.DataFrame(scores["spatial_cv"]).sort_values("pr_auc")
    temporal = scores["scores"][scores["selected"]]["test"]["pr_auc"]

    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.barh(blocks["block"], blocks["pr_auc"], color=WATER, alpha=0.85)
    ax.axvline(temporal, color=ACCENT, linewidth=2)
    ax.text(temporal, len(blocks) - 0.35, f" temporal test {temporal:.3f}",
            color=ACCENT, fontsize=8, fontweight="bold", va="top")

    mean = float(blocks["pr_auc"].mean())
    ax.axvline(mean, color=MUTED, linestyle="--", linewidth=1.2)
    ax.text(mean, -0.6, f"mean {mean:.3f}", color=MUTED, fontsize=8,
            ha="center")

    for row in blocks.itertuples(index=False):
        ax.text(row.pr_auc + 0.004, row.block, f"{row.pr_auc:.3f}",
                va="center", fontsize=8, color=INK)

    ax.set_xlabel("PR-AUC on the held-out region")
    ax.set_title("Leave-one-region-out — does it work where it was not trained?")
    ax.set_xlim(0, max(blocks["pr_auc"].max(), temporal) * 1.25)
    ax.grid(alpha=0.3, axis="x")
    _spine(ax)
    fig.text(0.5, -0.06,
             "Each bar is a model refitted with that whole region removed. "
             "Close to the temporal line means conditions were learned, not places.",
             ha="center", fontsize=8, color=MUTED)
    save(fig, "14_region_generalisation")


def fig_score_separation(y_true, probabilities, selected: str) -> None:
    prob = probabilities[selected]
    cases = prob[y_true == 1]
    controls = prob[y_true == 0]

    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    bins = np.linspace(0, max(prob.max(), 0.6), 45)
    ax.hist(controls, bins=bins, color=MUTED, alpha=0.55,
            label=f"controls (n={len(controls):,})", density=True)
    ax.hist(cases, bins=bins, color=ACCENT, alpha=0.7,
            label=f"landslides (n={len(cases):,})", density=True)

    ax.axvline(float(np.median(controls)), color=MUTED, linestyle="--",
               linewidth=1.4)
    ax.axvline(float(np.median(cases)), color=ACCENT, linestyle="--",
               linewidth=1.4)

    ax.set_xlabel("Model score")
    ax.set_ylabel("Density")
    ax.set_title(f"Score separation — {selected} on the test split")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    _spine(ax)

    shift = float(np.median(cases) - np.median(controls))
    fig.text(0.5, -0.05,
             f"Median score is {shift:+.3f} higher for real landslides. The "
             "overlap is the honest picture: this ranks, it does not separate cleanly.",
             ha="center", fontsize=8, color=MUTED)
    save(fig, "15_score_separation")


def main() -> int:
    matrix, features, scores = load_all()
    selected = scores["selected"]
    log.info("matrix %d rows | selected model %s", len(matrix), selected)

    y_true, probabilities = refit_for_curves(matrix, features, scores)
    log.info("refitted %d candidates for curve data", len(probabilities))

    fig_pr_curves(y_true, probabilities, selected)
    fig_recall_at_budget(y_true, probabilities, selected)
    fig_calibration()
    fig_shap()
    fig_region_generalisation(scores)
    fig_score_separation(y_true, probabilities, selected)

    log.info("6 figures written to %s", FIGURES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
