"""Slopewatch field application.

Power BI serves the authority-facing command centre. This covers what it cannot:
the field officer and citizen side of the problem statement — an interactive
risk map, a per-cell explanation in plain language, and geo-tagged reporting of
cracks and blocked roads from places with poor connectivity.

Reports are queued locally first and pushed when the database is reachable, so
an officer standing on a severed road can still file one.

    .venv/Scripts/streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src import db                               # noqa: E402
from src.model.scoring import RISK_BANDS         # noqa: E402

BAND_COLOURS = {
    "critical": "#9C332B",
    "high": "#C4622D",
    "elevated": "#B4762A",
    "moderate": "#6E7B4F",
    "low": "#2F6B57",
}
BAND_ORDER = [name for _, name in RISK_BANDS]

QUEUE_PATH = settings.DATA_DIR / "field_reports_queue.jsonl"
FIGURES = settings.PROJECT_ROOT / "reports" / "figures"
MODELS_DIR = settings.PROJECT_ROOT / "models"

st.set_page_config(
    page_title="Slopewatch",
    page_icon="⛰",
    layout="wide",
)

# The palette is shared with reports/figures and the walkthrough document. Left
# in one place so a colour never has to be remembered in three files.
THEME_CSS = """
<style>
  .stApp { background: #F4F6F4; }
  h1, h2, h3 { letter-spacing: -0.01em; }
  h1 { font-weight: 700; }
  [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
  [data-testid="stMetric"] {
      background: #FFFFFF;
      border: 1px solid #C6CEC9;
      border-radius: 2px;
      padding: 0.7rem 0.9rem;
  }
  [data-testid="stSidebar"] { background: #E7EBE8; border-right: 1px solid #C6CEC9; }
  .sw-note {
      border-left: 3px solid #9A5615;
      background: #F0E2D2;
      padding: 0.75rem 1rem;
      margin: 0.75rem 0;
      font-size: 0.92rem;
  }
  .sw-warn {
      border-left: 3px solid #8F2222;
      background: #FFFFFF;
      padding: 0.75rem 1rem;
      margin: 0.75rem 0;
      font-size: 0.92rem;
  }
  .sw-k {
      font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
      font-size: 0.7rem; color: #9A5615; display: block; margin-bottom: 0.2rem;
  }
  .sw-warn .sw-k { color: #8F2222; }
</style>
"""


def note(kind: str, label: str, body: str) -> None:
    """A callout that matches the ones in the walkthrough document."""
    st.markdown(
        f'<div class="sw-{kind}"><span class="sw-k">{label}</span>{body}</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------

@st.cache_data(ttl=900)
def load_risk(target_date: int | None = None) -> pd.DataFrame:
    where = "WHERE r.date_id = :target" if target_date else ""
    frame = db.read_sql(
        f"""
        SELECT  r.cell_id, r.date_id, r.full_date, r.probability,
                r.absolute_probability, r.risk_band,
                r.priority_score, r.driver_1, r.driver_2, r.driver_3,
                r.lat_c, r.lon_c, r.state_name, r.elev_mean, r.slope_mean,
                r.road_km_total, r.settlements, r.est_population
        FROM    mart_cell_daily_risk r
        {where}
        """,
        {"target": target_date} if target_date else None,
    )
    return frame


@st.cache_data(ttl=3600)
def load_available_dates() -> list[int]:
    frame = db.read_sql(
        "SELECT DISTINCT date_id FROM fact_risk_pred ORDER BY date_id"
    )
    return frame["date_id"].tolist()


@st.cache_data(ttl=3600)
def load_history() -> pd.DataFrame:
    return db.read_sql("""
        SELECT  state, year, month_name, events, deaths, elevation_band
        FROM    mart_event_history
    """)


# --------------------------------------------------------------------------
# offline-tolerant reporting
# --------------------------------------------------------------------------

def queue_report(record: dict) -> None:
    """Append a field report locally. Never blocks on the network."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def queued_reports() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@st.cache_data(ttl=900)
def load_metrics() -> dict:
    path = MODELS_DIR / "metrics_v1.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------

def model_page() -> None:
    """How well it works, stated before anyone has to ask.

    Every figure here is written by scripts/14_report_figures.py from the same
    artefacts the README and the walkthrough quote, so the app cannot show a
    number the rest of the project disagrees with.
    """
    st.title("Model")

    scores = load_metrics()
    if not scores:
        st.warning("models/metrics_v1.json is missing — run scripts/07_train_model.py.")
        return

    selected = scores["selected"]
    test = scores["scores"][selected]["test"]
    base = float(test["base_rate"])

    st.caption(
        f"**{selected}**, calibrated on the validation split, trained on "
        f"{scores['rows']:,} rows across {scores['features']} features."
    )

    columns = st.columns(5)
    columns[0].metric("PR-AUC", f"{test['pr_auc']:.3f}",
                      help=f"95% CI {test['pr_auc_lo']:.3f} – {test['pr_auc_hi']:.3f}")
    columns[1].metric("Base rate", f"{base:.3f}",
                      help="What random ranking would score. PR-AUC must be read against this.")
    columns[2].metric("Lift", f"{test['pr_auc'] / base:.2f}×")
    columns[3].metric("Recall @5%", f"{test['recall_at_5pct']:.1%}",
                      help="Share of real landslides inside the top 5% of cells")
    columns[4].metric("Brier", f"{test['brier']:.3f}",
                      help="Lower is better. Measures whether stated probabilities are true.")

    note("note", "Read the lift, not the PR-AUC",
         "A PR-AUC of "
         f"{test['pr_auc']:.3f} against a base rate of {base:.3f} is a "
         f"{test['pr_auc'] / base:.2f}× improvement on guessing. The headline "
         "number moves whenever the sample composition changes, which is why "
         "the bootstrap interval is shown beside it and why two runs are never "
         "compared directly.")

    st.subheader("What it delivers at an inspection budget")
    _figure("11_recall_at_budget.png",
            "The operational question: if a district can visit 5% of its cells "
            "today, what share of real landslides does it reach? This is also "
            "what decides which model ships — the three PR-AUCs tie, these do not.")

    left, right = st.columns(2)
    with left:
        st.subheader("Precision and recall")
        _figure("10_pr_curves.png", None)
    with right:
        st.subheader("What the model uses")
        _figure("13_shap.png", None)

    st.subheader("Does it work where it was not trained?")
    _figure("14_region_generalisation.png",
            "Each bar is the model refitted with that entire region removed. "
            "Landing near the temporal-test line means it learned conditions "
            "rather than places.")

    left, right = st.columns(2)
    with left:
        st.subheader("Calibration")
        _figure("12_calibration.png", None)
    with right:
        st.subheader("Score separation")
        _figure("15_score_separation.png", None)

    st.subheader("Model comparison")
    comparison = pd.DataFrame({
        name: {
            "PR-AUC": entry["test"]["pr_auc"],
            "CI low": entry["test"]["pr_auc_lo"],
            "CI high": entry["test"]["pr_auc_hi"],
            "Recall @5%": entry["test"]["recall_at_5pct"],
            "Recall @10%": entry["test"]["recall_at_10pct"],
            "Brier": entry["test"]["brier"],
        }
        for name, entry in scores["scores"].items()
    }).T.round(4)
    comparison.index = [
        f"{name}  ←" if name == selected else name for name in comparison.index
    ]
    st.dataframe(comparison, width="stretch")

    note("warn", "What this model is not",
         "It ranks cells for inspection. It does not issue warnings, it cannot "
         "certify a location as safe, and an 11 km cell is not a hillside. "
         "Landslides trigger on sub-daily rainfall intensity; this sees 24-hour "
         "totals, which is the single largest cap on what it can achieve.")


def _figure(name: str, caption: str | None) -> None:
    path = FIGURES / name
    if not path.exists():
        st.info(f"{name} not generated yet — run scripts/14_report_figures.py.")
        return
    st.image(str(path), width="stretch")
    if caption:
        st.caption(caption)

def risk_map_page() -> None:
    st.title("Slopewatch")
    st.caption(
        "Landslide and flash-flood risk across the Indian hill states. "
        "Scores are **relative risk**, not a chance of failure: the model is "
        "trained on a sample where one row in six is a landslide, while the "
        "real rate is about one cell-day in 48,000. A band of *elevated* means "
        "this cell is running at two to three times the background rate — it "
        "does not mean it is two-thirds likely to fail."
    )

    dates = load_available_dates()
    if not dates:
        st.warning(
            "No predictions stored yet. Run `scripts/08_score.py` to populate "
            "the forecast."
        )
        return

    selected = st.select_slider(
        "Forecast day", options=dates, value=dates[0],
        format_func=lambda value: str(pd.to_datetime(str(value)).date()),
    )
    frame = load_risk(selected)
    if frame.empty:
        st.info("Nothing scored for that day.")
        return

    counts = frame["risk_band"].value_counts()
    columns = st.columns(len(BAND_ORDER))
    for column, band in zip(columns, reversed(BAND_ORDER)):
        column.metric(band.title(), int(counts.get(band, 0)))

    # Default to the bands that are actually populated, highest first. A fixed
    # default of critical/high/elevated renders an empty map on a quiet day,
    # which reads as a broken app rather than as good news.
    present = [band for band in BAND_ORDER if counts.get(band, 0) > 0]
    default_bands = present[:3] if present else BAND_ORDER[:3]

    bands = st.multiselect("Show bands", BAND_ORDER, default=default_bands)
    shown = frame[frame["risk_band"].isin(bands)] if bands else frame

    if shown.empty:
        st.info("No cells in the selected bands.")
        return

    plot = shown.rename(columns={"lat_c": "latitude", "lon_c": "longitude"})
    plot["colour"] = plot["risk_band"].map(BAND_COLOURS)
    st.map(plot, latitude="latitude", longitude="longitude",
           color="colour", size=4000)

    st.subheader("Highest priority")
    st.caption(
        "Ranked by risk weighted by what is downhill — a moderate cell above a "
        "road and two villages outranks a severe one in empty forest."
    )
    table = (
        shown.nlargest(20, "priority_score")[[
            "state_name", "lat_c", "lon_c", "risk_band", "probability",
            "priority_score", "settlements", "est_population",
            "road_km_total", "driver_1",
        ]]
        .rename(columns={
            "state_name": "State", "lat_c": "Lat", "lon_c": "Lon",
            "risk_band": "Band", "probability": "Risk score",
            "priority_score": "Priority", "settlements": "Villages",
            "est_population": "People", "road_km_total": "Road km",
            "driver_1": "Main driver",
        })
    )
    st.dataframe(table, width="stretch", hide_index=True)


def cell_detail_page() -> None:
    st.title("Cell detail")

    dates = load_available_dates()
    if not dates:
        st.warning("No predictions stored yet.")
        return

    frame = load_risk(dates[0])
    if frame.empty:
        st.info("Nothing scored.")
        return

    options = frame.nlargest(200, "priority_score")
    labels = {
        int(row.cell_id): (
            f"{row.state_name or 'unknown'} — "
            f"{row.lat_c:.2f}N {row.lon_c:.2f}E — {row.risk_band}"
        )
        for row in options.itertuples(index=False)
    }
    cell_id = st.selectbox(
        "Cell", list(labels), format_func=lambda value: labels[value]
    )

    series = load_risk()
    cell = series[series["cell_id"] == cell_id].sort_values("date_id")
    if cell.empty:
        st.info("No data for that cell.")
        return

    latest = cell.iloc[0]
    left, right = st.columns([1, 2])

    with left:
        # Shown as a bare score, never as a percentage. Formatting 0.233 as
        # "23.3%" invites it to be read as a chance of failure, which it is not
        # — see the absolute figure below for that.
        st.metric("Risk score", f"{latest['probability']:.3f}")
        st.metric("Band", str(latest["risk_band"]).title())
        st.metric("Priority", f"{latest['priority_score']:.3f}")

        absolute = latest.get("absolute_probability")
        if absolute is not None and absolute == absolute:
            odds = int(round(1 / max(float(absolute), 1e-12)))
            st.caption(
                f"Actual chance of failure on this day: about **1 in {odds:,}**. "
                "The score above ranks cells against each other; this is the "
                "real frequency."
            )
        st.write("**Terrain**")
        st.write(f"Elevation {latest['elev_mean']:.0f} m")
        st.write(f"Mean slope {latest['slope_mean']:.1f}°")
        st.write("**Exposed**")
        st.write(f"{int(latest['settlements'])} settlements")
        st.write(f"{int(latest['est_population']):,} people")
        st.write(f"{latest['road_km_total']:.1f} km of road")

    with right:
        st.write("**Why this cell is rated as it is**")
        drivers = [latest[f"driver_{index}"] for index in (1, 2, 3)]
        drivers = [driver for driver in drivers if driver]
        if drivers:
            for rank, driver in enumerate(drivers, start=1):
                st.write(f"{rank}. {driver}")
        else:
            st.caption("No attribution stored for this prediction.")

        st.write("**Risk score across the forecast window**")
        chart = cell.set_index("full_date")[["probability"]]
        st.line_chart(chart)


def field_report_page() -> None:
    st.title("Report from the field")
    st.caption(
        "Cracks, slope movement, blocked roads. Reports are saved on this "
        "device first, so one can be filed without a connection and pushed "
        "when the network returns."
    )

    with st.form("field_report"):
        columns = st.columns(2)
        latitude = columns[0].number_input(
            "Latitude", value=25.57, min_value=float(settings.LAT_MIN),
            max_value=float(settings.LAT_MAX), format="%.5f",
        )
        longitude = columns[1].number_input(
            "Longitude", value=91.88, min_value=float(settings.LON_MIN),
            max_value=float(settings.LON_MAX), format="%.5f",
        )

        observation = st.selectbox(
            "What did you see?",
            ["Cracks in slope", "Slope movement", "Road blocked",
             "Debris flow", "Bridge damage", "Other"],
        )
        severity = st.select_slider(
            "Severity", ["minor", "moderate", "serious", "severe"],
            value="moderate",
        )
        notes = st.text_area("Notes", max_chars=1000)
        photo = st.file_uploader("Photo", type=["jpg", "jpeg", "png"])
        reporter = st.text_input("Your name or ID")

        submitted = st.form_submit_button("Submit report")

    if submitted:
        if not reporter.strip():
            st.error("Add your name or ID so the report can be followed up.")
            return

        record = {
            "reported_at": datetime.now(timezone.utc).isoformat(),
            "latitude": latitude,
            "longitude": longitude,
            "observation": observation,
            "severity": severity,
            "notes": notes.strip(),
            "reporter": reporter.strip(),
            "photo_name": photo.name if photo else None,
        }
        queue_report(record)
        st.success("Report saved. It will sync when the network is available.")

    pending = queued_reports()
    if pending:
        st.subheader(f"Queued reports ({len(pending)})")
        st.dataframe(pd.DataFrame(pending), width="stretch", hide_index=True)


def history_page() -> None:
    st.title("Recorded history")
    st.caption(
        "Landslides recorded in the NASA Global Landslide Catalog across the "
        "study area, 2007 to 2016. These are the labels the model learned from."
    )

    frame = load_history()
    if frame.empty:
        st.warning("mart_event_history is empty — run sql/02_analytics.sql.")
        return

    by_state = (
        frame.groupby("state")[["events", "deaths"]].sum()
        .sort_values("events", ascending=False).head(15)
    )
    st.subheader("Events by state")
    st.bar_chart(by_state["events"])

    st.subheader("Deaths by state")
    st.caption("Uttarakhand is dominated by the 2013 Kedarnath disaster.")
    st.bar_chart(by_state["deaths"])

    st.subheader("Seasonality")
    by_month = frame.groupby("month_name")["events"].sum()
    order = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
    st.bar_chart(by_month.reindex([m for m in order if m in by_month.index]))


PAGES = {
    "Risk map": risk_map_page,
    "Cell detail": cell_detail_page,
    "Model": model_page,
    "Field report": field_report_page,
    "History": history_page,
}


def main() -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)
    st.sidebar.title("Slopewatch")
    st.sidebar.caption("SIH 2026 · PS 26192")
    choice = st.sidebar.radio("View", list(PAGES))
    st.sidebar.divider()
    st.sidebar.caption(
        "Prototype decision support. Predictions are advisory and do not "
        "replace an official warning from the state disaster authority."
    )

    try:
        PAGES[choice]()
    except Exception as exc:
        st.error(f"Could not load this view: {exc}")
        st.caption(
            "The warehouse may be unreachable. Check that MySQL is running and "
            "that .env points at it."
        )


if __name__ == "__main__":
    main()
