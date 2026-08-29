# Model card — Slopewatch risk model v1

A daily landslide susceptibility score for 0.1° (~11 km) cells in the Indian
Himalaya, driven by antecedent rainfall, soil moisture and terrain.

| | |
|---|---|
| version | `v1` — `models/risk_model_v1.joblib` |
| type | logistic regression, median imputation → standardisation → L2 (`C=0.5`), balanced class weights |
| calibration | Platt scaling fitted on the validation split only |
| inputs | 41 features — rolling rainfall, soil moisture, evapotranspiration, wind, terrain, seasonality |
| output | probability on the **sampled** base rate, prior-corrected to the population rate at scoring time |
| trained on | 2,108 rows / 465 positives (2007–2013) |
| produced by | `scripts/07_train_model.py`, metrics in `models/metrics_v1.json` |

Logistic was selected over random forest (test PR-AUC 0.366) and XGBoost
(0.334). Both trees showed a large train-test gap at this sample size; the
linear model did not.

---

## Intended use

Prioritising **where to look** across a large area on a given day — ranking
cells so limited inspection, patrol or pre-positioning effort goes to the top of
a list rather than being spread evenly.

It is a screening aid for planners and analysts who can read a calibrated
probability alongside its uncertainty.

## Out of scope

Do not use this model to:

- **Issue public warnings or order evacuations.** It has no hourly nowcast, no
  ground-truth validation against IMD or GSI bulletins, and no human in the loop.
- **Decide anything at a single point.** An 11 km cell is not a hillside. The
  score describes a cell, not a road cutting, a village, or a slope.
- **Certify a location as safe.** A low score reflects the absence of the
  conditions this model can see. See *reporting bias* below.
- **Read a score as a probability of a landslide.** Scores are calibrated on the
  sampled base rate (~0.23), not the population rate (~1 in 48,000 cell-days).
  The prior correction at scoring time converts them; the raw model output does
  not.

---

## Performance

Test split — 2015–2016, temporally held out, 573 rows / 153 positives.

| metric | value |
|---|---|
| PR-AUC | **0.410** (95% CI 0.345 – 0.482) |
| base rate | 0.267 |
| ROC-AUC | 0.688 |
| Brier | 0.184 |
| recall @ 1% budget | 0.033 |
| recall @ 5% budget | 0.111 |
| recall @ 10% budget | 0.203 |
| recall @ 20% budget | 0.431 |

**Read the budget rows, not the AUC.** They are what the model would actually
deliver: inspecting the top 10% of cells catches about 1 event in 5. Whether
that is useful depends entirely on what inspecting a cell costs.

The confidence interval is bootstrapped and is printed everywhere on purpose.
The test split grows as the weather backfill lands, so two successive runs are
not measured on the same data and their point estimates are not comparable.

### Generalisation across regions

Leave-one-region-out, four blocks:

| held-out block | PR-AUC | recall @ 5% |
|---|---|---|
| central Himalaya | 0.436 | 0.184 |
| west Himalaya | 0.388 | 0.114 |
| north east | 0.359 | 0.131 |
| eastern Himalaya | 0.339 | 0.072 |

Mean 0.380 against a temporal-test 0.410. The gap is small, which is the point
of the exercise: performance does not collapse on terrain the model has not
seen. Kashmir is deliberately grouped with Himachal rather than pooled — only
47% of its events fall in the monsoon against 85–93% elsewhere, because it is
driven by western disturbances and snowmelt.

### Calibration

Mean absolute gap 0.065 across bins. Reliable in the two bins that carry the
data (211 and 300 rows); the 0.3–0.4 bin is over-confident (predicted 0.371,
observed 0.542) and the bins above 0.6 hold one row each and mean nothing.

---

## What the model uses

Top features by mean |SHAP|:

| # | feature | |SHAP| |
|---|---|---|
| 1 | `api` — antecedent precipitation index | 0.660 |
| 2 | `sm_28_100_mean_7d` — deep soil moisture, 7-day mean | 0.512 |
| 3 | `sm_0_7_mean_7d` — surface soil moisture, 7-day mean | 0.505 |
| 4 | `sm_7_28` | 0.499 |
| 5 | `sm_28_100` | 0.402 |
| 6 | `sm_0_7` | 0.390 |
| 7 | `slope_mean` | 0.369 |

Antecedent wetness first, terrain seventh. That ordering is the physics the
landslide literature describes, and it is the result of a fix — see below.

---

## Known failure modes and biases

**The elevation confounder (fixed, and worth stating).** An earlier version of
this model put `elev_mean` at the top of the SHAP ranking with twice the weight
of anything else. The cause was the sampler, not the terrain: background
controls were drawn uniformly from a hill mask that admits the whole Ladakh and
Tibetan plateau, giving them a median elevation of 4,479 m against 1,433 m for
cases. The model had learned to separate a plateau from a slope. Elevation-band
matching fixed it; `elev_mean` no longer appears in the top 15 and the
case-control gap is −17 m. A regression test guards it. **Anyone reusing this
sampler on a new region must re-run that audit** — the hill mask is a slope
threshold, and slope thresholds admit plateaus.

**Reporting bias.** The NASA Global Landslide Catalog records landslides that
were *reported*. Coverage skews toward roads, settlements and media attention,
so remote terrain is under-represented in the positives and over-represented in
the unlabelled background. Candidate negatives near a known event are discarded
rather than labelled zero, which mitigates but does not remove this.

**Resolution mismatch — the main performance cap.** Landslides trigger on
sub-daily rainfall intensity. The model sees `precipitation_sum`, a 24-hour
total on an 11 km grid. A three-hour cloudburst and a day of drizzle can be the
same number here and are entirely different on a hillside. Orographic rainfall
varies over 1–2 km; the predictor frequently does not see the rain that caused
the slide. This is a data-resolution limit, not a model-class limit, and no
amount of additional rows of the same features will lift it.

**Sample size.** Trained on 2,108 rows because the weather backfill is
incomplete — Open-Meteo's free tier allows ~10,000 calls/day against ~38,000
needed. Events-per-variable is 11 across 41 features, which is thin.
`scripts/13_learning_curve.py` measures the cost: the curve is flattening, so
completing the backfill buys statistical confidence rather than a large score
gain.

**Temporal scope.** 2007–2016. Not validated against post-2016 conditions, and
not adjusted for trend in extreme-rainfall frequency over that period.

---

## Ethical considerations

This model scores hazard, not risk to people. Exposure — roads and settlements
in `fact_exposure` — is joined downstream for reporting and is **not** part of
the model. Ranking by hazard alone will under-serve sparsely populated areas
that are nonetheless dangerous to the people in them; ranking by exposure alone
will over-serve cities. Any deployment must state which it is ranking by.

A false negative here is an un-inspected slope. Present low scores as *"no
signal in what we measured"* rather than as *safe*, and pair every published
score with the recall-at-budget figures above so the miss rate is visible rather
than implied.

---

## Reproducing

```bash
.venv/Scripts/python.exe scripts/run_pipeline.py --from 06
```

```bash
.venv/Scripts/python.exe -m pytest
```

Artefacts: `models/metrics_v1.json`, `models/feature_importance_v1.csv`,
`models/calibration_v1.csv`, `models/learning_curve_logistic.csv`.
Verification: `scripts/10_verify.py`, 24 checks across completeness, integrity,
leakage and honesty.
