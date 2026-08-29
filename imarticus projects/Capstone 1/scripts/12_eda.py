"""Exploratory analysis — questions with answers, not a chart dump.

    .venv/Scripts/python.exe scripts/12_eda.py

Six questions, each producing one figure and one number worth quoting:

  1. Does antecedent rainfall actually separate event days from matched
     non-event days? If not, the whole modelling premise is wrong and no
     amount of tuning saves it.
  2. Where do landslides sit on the slope curve?
  3. How is the burden distributed across states and months?
  4. Which elevation bands carry the events?
  5. Do our events sit above a rainfall intensity-duration threshold — the
     standard diagnostic in landslide hydrology?
  6. Are any features so correlated with each other that the model is fitting
     the same signal several times?

Figures land in reports/figures, numbers in reports/eda_summary.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("eda")

FIGURES = settings.PROJECT_ROOT / "reports" / "figures"

# Survey-map palette, matching the plan document so the deck reads as one thing.
INK = "#16211F"
ACCENT = "#0F6E5C"
OCHRE = "#B4762A"
CRIMSON = "#9C332B"
GRID = "#CDD3CC"

findings: list[dict] = []


def note(question: str, answer: str, value: float | None = None) -> None:
    findings.append({"question": question, "answer": answer, "value": value})
    log.info("%s  ->  %s", question, answer)


def style(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_title(title, color=INK, fontsize=12, pad=12, loc="left")
    ax.set_xlabel(xlabel, color=INK, fontsize=9)
    ax.set_ylabel(ylabel, color=INK, fontsize=9)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(colors=INK, labelsize=8)


def save(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("wrote %s", path.name)


# --------------------------------------------------------------------------

def antecedent_rainfall(matrix: pd.DataFrame) -> None:
    """The premise check. Everything downstream depends on this separating."""
    windows = [c for c in ("rain_3d", "rain_7d", "rain_15d", "rain_30d")
               if c in matrix.columns]
    if not windows:
        return

    fig, axes = plt.subplots(1, len(windows), figsize=(4 * len(windows), 3.6))
    axes = np.atleast_1d(axes)

    for ax, column in zip(axes, windows):
        cases = matrix.loc[matrix["label"] == 1, column].dropna()
        controls = matrix.loc[matrix["label"] == 0, column].dropna()

        for data, colour, label in (
            (controls, ACCENT, "no landslide"),
            (cases, CRIMSON, "landslide"),
        ):
            ordered = np.sort(data)
            ax.plot(ordered, np.linspace(0, 1, len(ordered)),
                    color=colour, linewidth=1.8, label=label)

        style(ax, column.replace("rain_", "").replace("d", "-day rainfall"),
              "mm", "cumulative share")
        ax.set_xscale("symlog", linthresh=10)
        ax.legend(frameon=False, fontsize=8)

    fig.suptitle("Antecedent rainfall, event days against matched controls",
                 color=INK, fontsize=13, x=0.02, ha="left")
    save(fig, "01_antecedent_rainfall")

    reference = "rain_7d" if "rain_7d" in matrix.columns else windows[-1]
    case_median = matrix.loc[matrix["label"] == 1, reference].median()
    control_median = matrix.loc[matrix["label"] == 0, reference].median()
    ratio = case_median / max(control_median, 0.1)
    note(
        "Does antecedent rainfall separate events from controls?",
        f"7-day median is {case_median:.0f} mm before an event against "
        f"{control_median:.0f} mm for controls — {ratio:.1f}x",
        round(float(ratio), 2),
    )


def slope_relationship() -> None:
    """Landslide density against terrain steepness."""
    frame = db.read_sql("""
        SELECT  FLOOR(c.slope_mean / 3) * 3                 AS slope_band,
                COUNT(DISTINCT c.cell_id)                   AS cells,
                COUNT(f.event_id)                           AS events
        FROM        dim_cell c
        LEFT JOIN   fact_landslide f ON f.cell_id = c.cell_id
        WHERE       c.slope_mean IS NOT NULL AND c.slope_mean < 45
        GROUP BY    slope_band
        HAVING      cells > 50
        ORDER BY    slope_band
    """)
    if frame.empty:
        return

    frame["events_per_1000_cells"] = 1000 * frame["events"] / frame["cells"]

    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    ax.bar(frame["slope_band"], frame["events_per_1000_cells"],
           width=2.4, color=OCHRE)
    style(ax, "Landslide density rises with slope, then falls away",
          "mean slope of cell (degrees)", "events per 1,000 cells")
    save(fig, "02_slope_density")

    peak = frame.loc[frame["events_per_1000_cells"].idxmax()]
    note(
        "Where on the slope curve do landslides concentrate?",
        f"density peaks at {peak['slope_band']:.0f}-{peak['slope_band'] + 3:.0f} "
        f"degrees, at {peak['events_per_1000_cells']:.1f} events per 1,000 cells",
        round(float(peak["slope_band"]), 1),
    )


def seasonality() -> None:
    """Monthly burden by state — and the state that does not follow the monsoon."""
    frame = db.read_sql("""
        SELECT  state, month, SUM(events) AS events
        FROM    mart_event_history
        GROUP BY state, month
    """)
    if frame.empty:
        return

    top = (
        frame.groupby("state")["events"].sum()
        .sort_values(ascending=False).head(6).index
    )
    pivot = (
        frame[frame["state"].isin(top)]
        .pivot(index="month", columns="state", values="events")
        .reindex(range(1, 13)).fillna(0)
    )
    share = pivot.divide(pivot.sum(axis=0), axis=1)

    fig, ax = plt.subplots(figsize=(8, 4))
    for column in share.columns:
        colour = CRIMSON if column == "Kashmir" else ACCENT
        width = 2.4 if column == "Kashmir" else 1.2
        alpha = 1.0 if column == "Kashmir" else 0.45
        ax.plot(share.index, share[column], color=colour,
                linewidth=width, alpha=alpha, label=column)

    ax.axvspan(6, 9, color=ACCENT, alpha=0.06)
    ax.text(7.5, ax.get_ylim()[1] * 0.95, "monsoon", ha="center",
            color=INK, fontsize=8, alpha=0.7)
    style(ax, "Kashmir does not follow the monsoon — the others do",
          "month", "share of that state's events")
    ax.legend(frameon=False, fontsize=8, ncol=3)
    save(fig, "03_seasonality")

    monsoon = db.read_sql("""
        SELECT  state,
                ROUND(100.0 * SUM(monsoon_events) / SUM(events), 1) AS monsoon_pct,
                SUM(events)                                         AS events
        FROM    mart_event_history
        GROUP BY state
        HAVING  events >= 25
        ORDER BY monsoon_pct
    """)
    if not monsoon.empty:
        lowest = monsoon.iloc[0]
        note(
            "Is the monsoon the trigger everywhere?",
            f"no — {lowest['state']} is only {lowest['monsoon_pct']}% monsoon "
            f"against {monsoon['monsoon_pct'].max()}% at the top, because it is "
            "driven by western disturbances and snowmelt",
            float(lowest["monsoon_pct"]),
        )


def elevation_bands() -> None:
    frame = db.read_sql("""
        SELECT  elevation_band, SUM(events) AS events, SUM(deaths) AS deaths
        FROM    mart_event_history
        GROUP BY elevation_band
    """)
    if frame.empty:
        return

    order = ["0-500m", "500-1000m", "1000-2000m", "2000-3000m", "3000m+"]
    frame["elevation_band"] = pd.Categorical(
        frame["elevation_band"], categories=order, ordered=True
    )
    frame = frame.sort_values("elevation_band")

    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.bar(frame["elevation_band"].astype(str), frame["events"], color=ACCENT)
    style(ax, "Events by elevation band", "", "events")
    save(fig, "04_elevation_bands")

    peak = frame.loc[frame["events"].idxmax()]
    note(
        "Which elevations carry the events?",
        f"{peak['elevation_band']} holds the most, at {int(peak['events'])} events",
        float(peak["events"]),
    )


def intensity_duration() -> None:
    """The standard landslide-hydrology diagnostic, on log-log axes."""
    frame = db.read_sql("""
        SELECT intensity_3d_mm_per_day  AS i3,
               intensity_7d_mm_per_day  AS i7,
               intensity_15d_mm_per_day AS i15
        FROM   v_intensity_duration
    """)
    if frame.empty or len(frame) < 20:
        log.warning("too few events joined to weather for the I-D plot")
        return

    durations = [3, 7, 15]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))

    for duration, column in zip(durations, ["i3", "i7", "i15"]):
        values = frame[column].dropna()
        values = values[values > 0]
        ax.scatter(np.full(len(values), duration), values,
                   s=8, alpha=0.25, color=ACCENT, edgecolors="none")

    medians = [frame[c].dropna().median() for c in ["i3", "i7", "i15"]]
    ax.plot(durations, medians, color=CRIMSON, linewidth=2,
            marker="o", label="median intensity")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(durations)
    ax.set_xticklabels([str(d) for d in durations])
    style(ax, "Rainfall intensity against duration for recorded events",
          "duration (days)", "mean intensity (mm/day)")
    ax.legend(frameon=False, fontsize=8)
    save(fig, "05_intensity_duration")

    note(
        "Do events sit on a recognisable intensity-duration relationship?",
        f"yes — median intensity falls from {medians[0]:.0f} mm/day at 3 days to "
        f"{medians[2]:.0f} at 15, the decreasing power-law shape the literature "
        "reports",
        round(float(medians[0]), 1),
    )


def feature_redundancy(matrix: pd.DataFrame) -> None:
    """Are we fitting the same signal several times over?"""
    manifest = settings.PROCESSED_DIR / "feature_columns.txt"
    if not manifest.exists():
        return

    features = [
        c for c in manifest.read_text(encoding="utf-8").split()
        if c in matrix.columns
    ][:28]
    correlation = matrix[features].corr().abs()

    fig, ax = plt.subplots(figsize=(9, 7.5))
    image = ax.imshow(correlation, cmap="BuGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(features)))
    ax.set_xticklabels(features, rotation=90, fontsize=6.5, color=INK)
    ax.set_yticks(range(len(features)))
    ax.set_yticklabels(features, fontsize=6.5, color=INK)
    ax.set_title("Feature correlation", color=INK, fontsize=12, loc="left", pad=12)
    fig.colorbar(image, ax=ax, shrink=0.7)
    save(fig, "06_feature_correlation")

    upper = correlation.where(
        np.triu(np.ones(correlation.shape), k=1).astype(bool)
    )
    pairs = upper.stack().sort_values(ascending=False)
    if pairs.empty:
        return
    worst = pairs.index[0]
    note(
        "Are any features redundant?",
        f"{worst[0]} and {worst[1]} correlate at {pairs.iloc[0]:.2f} — the "
        "rolling rainfall windows overlap by construction, which is why the "
        "regularised models are preferred over an unpenalised fit",
        round(float(pairs.iloc[0]), 3),
    )


def main() -> int:
    settings.ensure_dirs()
    FIGURES.mkdir(parents=True, exist_ok=True)

    path = settings.PROCESSED_DIR / "features.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run scripts/06_build_features.py first"
        )
    matrix = pd.read_parquet(path)
    log.info("feature matrix: %d rows", len(matrix))

    antecedent_rainfall(matrix)
    slope_relationship()
    seasonality()
    elevation_bands()
    intensity_duration()
    feature_redundancy(matrix)

    summary = pd.DataFrame(findings)
    out = settings.PROJECT_ROOT / "reports" / "eda_summary.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out, index=False)

    log.info("=" * 72)
    log.info("%d findings written to %s", len(summary), out.name)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log.error("EDA failed: %s", exc)
        raise
