# Slopewatch

Daily landslide risk for the Indian Himalaya, on an 11 km grid, from rainfall
and terrain.

A landslide is a rare event — roughly **1 in 48,000 cell-days** across the study
area. That number is the whole problem. It rules out training on a gridded panel
(it would be 99.998% negatives and a model that predicts "no" everywhere scores
99.998% accurate), and it rules out reading any headline accuracy figure
literally. Everything below is built around that constraint.

---

## Result

| | test split |
|---|---|
| PR-AUC | **0.410** [0.345 – 0.482] |
| base rate (sampled) | 0.267 |
| ROC-AUC | 0.688 |
| Brier | 0.184 |
| mean held-out-region PR-AUC | 0.380 |
| calibration error | 0.065 mean abs. gap |

Selected model: **logistic regression**, Platt-calibrated on the validation
split. It beat random forest (0.366) and XGBoost (0.334) on the test split —
both trees overfit hard at this sample size, which the train/test gap makes
plain.

Intended use, out-of-scope use, failure modes and biases are in
[`docs/model-card.md`](docs/model-card.md). Read it before quoting a score.

**1.5× lift over base rate.** That is a real but modest signal, and the honest
reading is that the ceiling here is *predictor resolution*, not model choice —
see [Limitations](#limitations). The bracketed interval is a bootstrap 95% CI
and it is printed everywhere on purpose: the test split grows as the weather
backfill lands, so two successive runs are not measured on the same data and are
not directly comparable.

---

## The elevation confounder

The most important thing this project found, it found in its own sampler.

An early model put `elev_mean` at the top of the SHAP ranking with **twice the
weight of any other feature**. That is not physics. Background controls were
being drawn uniformly from the hill mask, which admits the entire Ladakh and
Tibetan plateau — giving them a median elevation of **4,479 m against 1,433 m
for cases**. The model had learned to tell a high plateau from a mid-elevation
slope. It had not learned anything about landslides.

The fix is elevation-band matching in `src/data/sampling.py`: background and
spatial controls are drawn from the elevation band of a case (500 m bands, ±1
band tolerance) instead of from the hill mask at large.

| top SHAP feature | before | after |
|---|---|---|
| 1 | `elev_mean` 0.513 | `api` 0.660 |
| 2 | `rain_max_1d_in_7` 0.221 | `sm_28_100_mean_7d` 0.512 |
| 3 | `rain_30d_anomaly` 0.208 | `sm_0_7_mean_7d` 0.505 |
| … | | |
| 7 | | `slope_mean` 0.369 |

After the fix `elev_mean` does not appear in the top 15 at all, and `slope_mean`
climbs from #11 to #7. Case-control elevation gap is now **−17 m**. The
regression test for this lives in `tests/test_sampling.py` and builds a world
where plateau and slope cells are interleaved in space, so distance alone cannot
separate them.

---

## Design

**Case-control, not a gridded panel.** Every recorded event is a case; negatives
are sampled around them in three strata, six per case:

| stratum | share | what it asks |
|---|---|---|
| temporal | 50% | same cell, a different season-matched date — *why did **this** day fail?* |
| spatial | 30% | same date, a cell 25–300 km away in the same elevation band |
| background | 20% | a random hill cell and date, drawn from the case elevation distribution |

Candidate negatives within the exclusion radius and window of a known event are
discarded rather than labelled zero. The catalogue records *reported*
landslides; unreported ones are common in remote terrain, and a silent false
negative is worse than a smaller sample.

**Splits are blocked, never random.** Train ≤ 2013, validation 2014, test 2015–16.
Leave-one-region-out cross-validation runs on top of that across four blocks.
Kashmir is deliberately grouped with Himachal rather than pooled: only 47% of
Kashmir's events fall in the monsoon against 85–93% elsewhere, because it is
driven by western disturbances and snowmelt. A model that generalises to Kashmir
has learned conditions rather than a calendar.

**Scores are prior-corrected at scoring time**, from the sampled base rate back
to the population rate. A 0.3 from this model is not a 30% chance of a landslide.

---

## How much data is enough?

Measured, not asserted. `scripts/13_learning_curve.py` subsamples the training
split and holds the test split fixed:

```
 frac   rows   pos   EPV   test pr_auc
 0.25    527   116   2.8   0.380 ± 0.018
 0.40    843   186   4.5   0.415 ± 0.023
 0.55   1160   256   6.2   0.424 ± 0.013
 0.70   1476   326   8.0   0.429 ± 0.024
 0.85   1792   395   9.6   0.443 ± 0.018
 1.00   2108   465  11.3   0.446
```

Quadrupling the training rows bought +0.066. The last 1.4× bought +0.016. The
curve is flattening, so finishing the weather backfill is worth doing for
**statistical power** — events-per-variable rises from 11 to ~28 across 41
features, which is the difference between "model selection is noise" and a
defensible choice — but not for a large jump in score.

---

## Limitations

Read these before quoting any number above.

- **Daily rainfall sums cannot see the trigger.** Landslides respond to
  sub-daily intensity (mm/hr). Two cells with identical `precipitation_sum` —
  one a three-hour cloudburst, one steady drizzle — are indistinguishable to
  this model and completely different to the slope. This is the single largest
  cap on performance.
- **11 km cells against 1–2 km orographic rainfall.** The grid matches ERA5-Land
  native resolution, so there is no false precision, but the predictor often does
  not see the rain that caused the slide.
- **Reporting bias in the catalogue.** NASA GLC records landslides that were
  *reported*, which skews toward roads, settlements and media coverage. Absence
  of a record is not absence of a landslide — hence the exclusion buffer.
- **The weather backfill is incomplete.** Open-Meteo's free tier allows ~10,000
  calls/day and the full sample needs ~38,000. Metrics recorded here are from a
  partial backfill; `models/metrics_v1.json` carries the row count for the run
  that produced them.
- **Not an operational warning system.** No hourly nowcast, no ground truth
  validation against IMD or GSI bulletins, no human in the loop.

---

## Running it

Requires Python 3.11, MySQL 8, and a `.env` with warehouse credentials
(see `config/settings.py` for the keys). The geospatial stack needs the project
virtualenv — system Python will not do.

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
```

Whole pipeline, resumable:

```bash
.venv/Scripts/python.exe scripts/run_pipeline.py
```

```bash
.venv/Scripts/python.exe scripts/run_pipeline.py --from 06
```

| stage | script | what it does |
|---|---|---|
| 00 | `00_init_db.py` | schema, `dim_date`, `dim_cell` |
| 01 | `01_load_landslides.py` | NASA Global Landslide Catalog → `fact_landslide` |
| 02 | `02_download_dem.py` | SRTM tiles |
| 03 | `03_build_terrain.py` | slope, TRI, elevation stats, hill mask |
| 11 | `11_label_admin.py` | state and district labels |
| 09 | `09_build_exposure.py` | OSM roads and settlements → `fact_exposure` |
| 04 | `04_build_sample.py` | case-control sample → `fact_sample` |
| 05 | `05_fetch_weather.py` | Open-Meteo archive backfill → `fact_weather_daily` |
| 06 | `06_build_features.py` | rolling rainfall/soil features + leakage audit |
| 07 | `07_train_model.py` | three models, spatial CV, SHAP, calibration |
| 08 | `08_score.py` | forecast pull and daily scoring → `fact_risk_pred` |
| 10 | `10_verify.py` | 24 warehouse, integrity, leakage and honesty checks |

The weather backfill is the long pole — days, not minutes. It is safe to
interrupt and resumes from its Parquet cache:

```bash
.venv/Scripts/python.exe scripts/05_fetch_weather.py --plan
```

```bash
.venv/Scripts/python.exe scripts/05_fetch_weather.py --no-discharge --daemon-hours 20
```

Interfaces:

```bash
.venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
```

Power BI measures are in `powerbi/measures.dax` (28 measures over the marts).

---

## Tests

```bash
.venv/Scripts/python.exe -m pytest
```

18 tests. They pin the three failures that actually happened here, rather than
chasing coverage:

- **`test_sampling.py`** — the elevation confounder cannot silently return.
- **`test_openmeteo.py`** — a permanent HTTP 400 costs one batch, not the run;
  cache windows stay anchored to their own sample so rebuilding the sample does
  not void the backfill.
- **`test_warehouse.py`** — no sample or weather row may be dated outside the
  study window. `dim_date` is *not* a pure dimension: `08_score.py` extends it
  with forecast days, and anything reading it unbounded inherits future dates.
  That is exactly how 18 samples came to be dated 2026 and how the archive
  fetcher came to ask for a window ERA5 has a five-day lag on.

Warehouse tests skip cleanly when MySQL is unreachable.

---

## Layout

```
config/      settings — every constant, with the reasoning next to it
src/
  data/      grid, DEM, terrain, catalogue, sampling, Open-Meteo, OSM, admin
  features/  rolling weather windows and the leakage audit
  model/     estimators, metrics with bootstrap intervals, SHAP, scoring
scripts/     00-13, one stage each, all runnable standalone
sql/         schema and the analytics views/marts
app/         Streamlit — risk map, events, model, data quality
powerbi/     DAX measures
tests/       regression cover for the three real failures
docs/        plan, handover, and a decisions log
reports/     EDA figures and the learning curve
```

## Data sources

- **NASA Global Landslide Catalog** — event locations and dates, 2007–2016.
- **Open-Meteo Archive** (ERA5-Land) — daily rainfall, soil moisture,
  temperature, ET₀, wind. Unauthenticated, ~10,000 calls/day.
- **Open-Meteo Flood** (GloFAS) — river discharge.
- **SRTM** — 30 m elevation, aggregated to slope/TRI per cell.
- **OpenStreetMap** — roads and settlements for exposure.
