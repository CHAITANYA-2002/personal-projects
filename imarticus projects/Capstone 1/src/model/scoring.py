"""Turning a model score into an instruction.

Three things happen here that the model itself does not do.

First, and most importantly, the model's output is not a probability of failure
and must not be presented as one. It was trained on a case-control sample where
roughly one row in six is a landslide; the real world is closer to one cell-day
in fifty thousand. A raw score of 0.23 therefore means "0.23 within the sampled
comparison", not "a 23% chance this slope fails" — and showing the second to a
district officer would be both alarming and false. `absolute_probability`
applies the standard case-control prior correction to recover a real frequency,
while the raw score is kept as a relative risk for ranking, which is what
actually directs inspection teams.

Second, risk bands. These are cut on the relative score, because that is what
ranks cells against each other today. The band names describe urgency of
inspection, not probability of collapse.

Third, priority. A cell above a district road and two villages outranks a
riskier one in empty forest, because the point of the system is to direct a
finite number of teams. Risk alone cannot do that; risk times exposure can.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Bands are cut on relative risk — the score divided by the sampled base rate —
# not on the raw score. Cutting on the raw score looks reasonable and fails
# silently: a model calibrated against a 16% base rate produces scores centred
# near 0.16, so a "critical at 0.80" rule never fires and the whole map reads
# "low" through a monsoon. Relative risk also carries the meaning an officer
# actually needs: 5x means five times the background rate for this sample.
RISK_BANDS = (
    (5.0, "critical"),
    (3.0, "high"),
    (2.0, "elevated"),
    (1.2, "moderate"),
    (0.0, "low"),
)

# Fallback when a model bundle predates the base-rate fields.
DEFAULT_BASE_RATE = 0.16

# Exposure weights. Lives first, then the connectivity whose loss isolates
# people for days, then the infrastructure that is expensive but replaceable.
EXPOSURE_WEIGHTS = {
    "est_population": 0.40,
    "settlements": 0.20,
    "road_km_total": 0.20,
    "health_facilities": 0.10,
    "schools": 0.10,
}


def absolute_probability(
    score: np.ndarray | pd.Series,
    sample_base_rate: float,
    population_base_rate: float,
) -> pd.Series:
    """Correct a case-control score back to a population frequency.

    Oversampling positives shifts the intercept of any calibrated classifier by
    a known amount, so it can be shifted back exactly: subtract the difference
    between the sampled and true log-odds of the base rate. This is the standard
    prior correction for case-control designs.

    Without it, every number the system reports is inflated by roughly four
    orders of magnitude — the model is not wrong, but its scale is, and a scale
    error in a public warning is not a small thing.
    """
    values = pd.Series(score).astype(float).clip(1e-9, 1 - 1e-9)

    sample_odds = sample_base_rate / (1 - sample_base_rate)
    population_odds = population_base_rate / (1 - population_base_rate)
    offset = np.log(sample_odds / population_odds)

    corrected_logit = np.log(values / (1 - values)) - offset
    return pd.Series(1 / (1 + np.exp(-corrected_logit)), index=values.index)


def relative_risk(
    score: np.ndarray | pd.Series,
    sample_base_rate: float | None = None,
) -> pd.Series:
    """How many times the background rate this cell-day is running at."""
    rate = sample_base_rate or DEFAULT_BASE_RATE
    return pd.Series(score).astype(float) / max(rate, 1e-9)


def assign_bands(
    probability: np.ndarray | pd.Series,
    sample_base_rate: float | None = None,
) -> pd.Series:
    """Map scores onto named urgency bands via relative risk.

    Cut on relative risk rather than on rank: a rank-based cut would paint the
    same 5% of the map red in January as in July, which makes the colour
    meaningless. These describe how urgently a cell should be inspected, not the
    chance it collapses — see absolute_probability for that.
    """
    ratios = relative_risk(probability, sample_base_rate)
    bands = pd.Series("low", index=ratios.index, dtype=object)

    # Ascending, so each higher threshold overwrites the one below it. Iterating
    # the table as written — highest first — lets the final (0.0, "low") rule
    # match everything and paint the entire map low, which is exactly the kind
    # of bug that looks like a quiet day rather than a broken function.
    for lower, name in sorted(RISK_BANDS):
        bands = bands.mask(ratios >= lower, name)
    return bands


def exposure_score(exposure: pd.DataFrame) -> pd.Series:
    """Normalise each exposure column to 0-1 and combine on the weights above.

    Percentile-normalised rather than min-max: a single cell containing a city
    would otherwise flatten every other cell to near zero.
    """
    score = pd.Series(0.0, index=exposure.index)
    for column, weight in EXPOSURE_WEIGHTS.items():
        if column not in exposure.columns:
            continue
        values = exposure[column].fillna(0).astype(float)
        if values.max() <= 0:
            continue
        score += weight * values.rank(pct=True)
    return score.clip(0, 1)


def priority(probability: pd.Series, exposure: pd.Series) -> pd.Series:
    """Rank cells for a limited number of inspection teams.

    Exposure raises priority but never zeroes it: an unpopulated cell about to
    fail is still worth knowing about, so the floor is 0.25 rather than 0.
    """
    weighted = 0.25 + 0.75 * exposure.reindex(probability.index).fillna(0)
    return (probability * weighted).round(4)


def top_drivers(
    shap_values: np.ndarray,
    feature_names: list[str],
    top_n: int = 3,
) -> list[list[str]]:
    """Per-row feature attributions, as names a person can read.

    Only positive contributions are returned: an officer needs to know what is
    pushing the risk up, not what is holding it down.
    """
    drivers: list[list[str]] = []
    for row in shap_values:
        order = np.argsort(-row)
        picked = [
            _humanise(feature_names[index])
            for index in order[:top_n]
            if row[index] > 0
        ]
        drivers.append(picked + [None] * (top_n - len(picked)))
    return drivers


_LABELS = {
    "rain_1d": "rainfall today",
    "rain_3d": "3-day rainfall",
    "rain_7d": "7-day rainfall",
    "rain_15d": "15-day rainfall",
    "rain_30d": "30-day rainfall",
    "rain_60d": "60-day rainfall",
    "rain_max_1d_in_7": "peak daily rain this week",
    "rain_max_1d_in_30": "peak daily rain this month",
    "rain_7d_anomaly": "rainfall above normal",
    "rain_30d_anomaly": "monthly rain above normal",
    "api": "accumulated wetness",
    "wet_days_7": "consecutive wet days",
    "wet_days_30": "wet days this month",
    "sm_0_7": "surface soil moisture",
    "sm_7_28": "shallow soil moisture",
    "sm_28_100": "deep soil moisture",
    "sm_0_7_delta_1d": "soil wetting today",
    "sm_0_7_delta_7d": "soil wetting this week",
    "wetness_ratio_7d": "rain against evaporation",
    "discharge": "river discharge",
    "discharge_ratio": "river above normal",
    "slope_mean": "mean slope",
    "slope_max": "steepest slope",
    "slope_std": "slope variability",
    "elev_mean": "elevation",
    "elev_range": "relief",
    "tri": "terrain ruggedness",
    "aspect_sin": "slope facing east-west",
    "aspect_cos": "slope facing north-south",
    "elev_min": "valley floor height",
    "elev_max": "ridge height",
    "rain_15d": "15-day rainfall",
    "et0_7d": "weekly evaporation",
    "temp_mean_7d": "weekly temperature",
    "month": "time of year",
    "doy_sin": "time of year",
    "doy_cos": "time of year",
    "has_prior_event": "past landslides here",
    "hist_events_before": "past landslides here",
    "is_monsoon": "monsoon season",
}


_DEPTH_NAMES = {
    "0_7": "surface",
    "7_28": "shallow",
    "28_100": "deep",
    "100_255": "bedrock",
}


def _humanise(name: str) -> str:
    """Turn a feature name into something an officer can read.

    The exact-match table handles the one-off names. Soil-moisture features are
    generated systematically — depth crossed with level, one-day change, weekly
    change and weekly mean — so they are decoded by rule rather than enumerated.
    Listing them individually works until the depths change, and then the app
    quietly starts showing "sm 7 28 delta 1d" to a district officer.
    """
    if name in _LABELS:
        return _LABELS[name]

    if name.startswith("sm_"):
        for depth, label in _DEPTH_NAMES.items():
            if not name.startswith(f"sm_{depth}"):
                continue
            suffix = name[len(f"sm_{depth}"):]
            return {
                "": f"{label} soil moisture",
                "_delta_1d": f"{label} soil wetting today",
                "_delta_7d": f"{label} soil wetting this week",
                "_mean_7d": f"{label} soil moisture this week",
            }.get(suffix, f"{label} soil moisture")

    return name.replace("_", " ")


def summarise(frame: pd.DataFrame) -> pd.DataFrame:
    """Band counts for a scored day — what the command centre header shows."""
    counts = (
        frame["risk_band"]
        .value_counts()
        .reindex([name for _, name in RISK_BANDS])
        .fillna(0)
        .astype(int)
    )
    return (
        counts.rename("cells")
        .rename_axis("risk_band")
        .reset_index()
    )
