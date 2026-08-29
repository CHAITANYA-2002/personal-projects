"""Feature engineering.

Three families, and the reason each exists:

  dynamic     what the sky and soil have been doing. Antecedent rainfall over
              several windows is the single strongest signal in the landslide
              literature — a slope fails because weeks of rain saturated it and
              then one burst tipped it, not because of the burst alone.

  static      terrain. Slope, ruggedness and aspect say which cells can fail at
              all. Constant per cell, so they carry no temporal information and
              cannot leak.

  contextual  calendar position and the cell's own recorded history.

Every feature here is computed the same way at training time and at scoring
time, because both call this module. That is deliberate: training/serving skew
is the most common way a system like this rots, and it fails silently.

The one real leakage risk is the historical-event count. It is computed as
"events in this cell strictly before this date", never as a total over the
whole catalogue — a model told how many landslides a cell will eventually have
is being handed the answer.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from config import settings

log = logging.getLogger(__name__)

# Capped by settings.FEATURE_WINDOW_DAYS: a window longer than the data fetched
# for each sample would produce a column that is null for every row, which is
# worse than absent because it survives into the model as a constant.
RAIN_WINDOWS = tuple(
    window for window in (1, 3, 7, 15, 30, 60)
    if window <= settings.FEATURE_WINDOW_DAYS
)
SOIL_COLUMNS = ("sm_0_7", "sm_7_28", "sm_28_100")
WET_DAY_MM = 1.0
API_DECAY = 0.92          # antecedent precipitation index, daily retention

STATIC_COLUMNS = (
    "elev_mean", "elev_range", "slope_mean", "slope_max", "slope_std",
    "aspect_sin", "aspect_cos", "tri",
)


def build_features(
    samples: pd.DataFrame,
    weather: pd.DataFrame,
    cells: pd.DataFrame,
    events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Assemble the model matrix.

    samples  cell_id, date_id, label, stratum, split, region_block
    weather  cell_id, date_id, precip_mm, sm_*, et0_mm, river_discharge, ...
    cells    cell_id plus the static terrain columns
    events   optional, for the historical-count features
    """
    _require_columns(samples, ("cell_id", "date_id"), "samples")
    _require_columns(weather, ("cell_id", "date_id", "precip_mm"), "weather")

    weather = weather.sort_values(["cell_id", "date_id"]).reset_index(drop=True)
    weather["date"] = pd.to_datetime(weather["date_id"].astype(str), format="%Y%m%d")

    log.info("computing rolling weather features over %d rows", len(weather))
    rolled = _rolling_weather(weather)

    frame = samples.merge(rolled, on=["cell_id", "date_id"], how="left")
    missing = frame["rain_7d"].isna().sum()
    if missing:
        log.warning(
            "%d of %d samples have no weather window — they will be dropped",
            missing, len(frame),
        )
        frame = frame.dropna(subset=["rain_7d"])

    frame = frame.merge(
        cells[["cell_id", *STATIC_COLUMNS]], on="cell_id", how="left"
    )
    frame = _calendar_features(frame)
    frame = _rain_anomaly(frame)

    if events is not None and not events.empty:
        frame = _history_features(frame, events)

    log.info("feature matrix: %d rows x %d columns", len(frame), frame.shape[1])
    return frame


# --------------------------------------------------------------------------
# dynamic
# --------------------------------------------------------------------------

def _rolling_weather(weather: pd.DataFrame) -> pd.DataFrame:
    grouped = weather.groupby("cell_id", sort=False)
    out = weather[["cell_id", "date_id"]].copy()

    precip = weather["precip_mm"].fillna(0.0)
    weather = weather.assign(_precip=precip)
    grouped = weather.groupby("cell_id", sort=False)

    for window in RAIN_WINDOWS:
        out[f"rain_{window}d"] = (
            grouped["_precip"]
            .rolling(window, min_periods=window)
            .sum()
            .reset_index(level=0, drop=True)
        )

    out["rain_max_1d_in_7"] = (
        grouped["_precip"].rolling(7, min_periods=7).max()
        .reset_index(level=0, drop=True)
    )
    out["rain_max_1d_in_30"] = (
        grouped["_precip"].rolling(30, min_periods=30).max()
        .reset_index(level=0, drop=True)
    )

    wet = (weather["_precip"] >= WET_DAY_MM).astype(float)
    weather = weather.assign(_wet=wet)
    grouped = weather.groupby("cell_id", sort=False)
    out["wet_days_7"] = (
        grouped["_wet"].rolling(7, min_periods=7).sum()
        .reset_index(level=0, drop=True)
    )
    out["wet_days_30"] = (
        grouped["_wet"].rolling(30, min_periods=30).sum()
        .reset_index(level=0, drop=True)
    )

    out["api"] = _antecedent_index(weather)

    for column in SOIL_COLUMNS:
        if column not in weather.columns:
            continue
        series = grouped[column]
        out[column] = weather[column].to_numpy()
        out[f"{column}_delta_1d"] = series.diff(1).reset_index(level=0, drop=True)
        out[f"{column}_delta_7d"] = series.diff(7).reset_index(level=0, drop=True)
        out[f"{column}_mean_7d"] = (
            series.rolling(7, min_periods=7).mean()
            .reset_index(level=0, drop=True)
        )

    if "et0_mm" in weather.columns:
        out["et0_7d"] = (
            grouped["et0_mm"].rolling(7, min_periods=7).sum()
            .reset_index(level=0, drop=True)
        )
        # Rain against evaporative demand: how much of what fell the ground
        # actually kept. This started as a subtraction (rain minus ET0) and had
        # to be changed — ET0 varies far less than rainfall, so the difference
        # correlated with rain_7d at 1.00 and carried no information the model
        # did not already have. A ratio is scale-free and does separate.
        out["wetness_ratio_7d"] = _safe_ratio(out["rain_7d"], out["et0_7d"])

    if "temp_mean" in weather.columns:
        out["temp_mean_7d"] = (
            grouped["temp_mean"].rolling(7, min_periods=7).mean()
            .reset_index(level=0, drop=True)
        )

    if "river_discharge" in weather.columns:
        out["discharge"] = weather["river_discharge"].to_numpy()
        window_mean = (
            grouped["river_discharge"].rolling(30, min_periods=10).mean()
            .reset_index(level=0, drop=True)
        )
        out["discharge_ratio"] = _safe_ratio(out["discharge"], window_mean)

    return out


def _antecedent_index(weather: pd.DataFrame) -> np.ndarray:
    """Exponentially decayed rainfall memory.

    API_t = precip_t + k * API_(t-1). A single number standing in for "how wet
    is this slope right now", weighting recent rain more than old rain without
    the hard cutoff a fixed window imposes.
    """
    values = np.empty(len(weather), dtype="float64")
    precip = weather["_precip"].to_numpy(dtype="float64")
    cell_ids = weather["cell_id"].to_numpy()

    running = 0.0
    previous_cell = None
    for index in range(len(weather)):
        if cell_ids[index] != previous_cell:
            running = 0.0
            previous_cell = cell_ids[index]
        running = precip[index] + API_DECAY * running
        values[index] = running
    return values


def _rain_anomaly(frame: pd.DataFrame) -> pd.DataFrame:
    """Rainfall relative to what is normal for that place at that time of year.

    200 mm in Cherrapunji and 200 mm in Leh are not the same event. Climatology
    is pooled by latitude band and day-of-year because the sampled windows are
    too short to build a reliable per-cell normal.
    """
    frame = frame.copy()
    if "lat_band" not in frame.columns:
        frame["lat_band"] = np.nan

    if "elev_mean" in frame.columns:
        frame["lat_band"] = (frame["elev_mean"] // 500).fillna(-1)

    keys = ["lat_band", "doy_bin"]
    climatology = (
        frame.groupby(keys, dropna=False)["rain_7d"]
        .transform("median")
        .replace(0, np.nan)
    )
    frame["rain_7d_anomaly"] = _safe_ratio(frame["rain_7d"], climatology)

    climatology_30 = (
        frame.groupby(keys, dropna=False)["rain_30d"]
        .transform("median")
        .replace(0, np.nan)
    )
    frame["rain_30d_anomaly"] = _safe_ratio(frame["rain_30d"], climatology_30)
    return frame.drop(columns=["lat_band"])


# --------------------------------------------------------------------------
# contextual
# --------------------------------------------------------------------------

def _calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    dates = pd.to_datetime(frame["date_id"].astype(str), format="%Y%m%d")

    frame["month"] = dates.dt.month
    day_of_year = dates.dt.dayofyear
    frame["doy_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    frame["doy_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
    frame["is_monsoon"] = frame["month"].isin(settings.MONSOON_MONTHS).astype(int)
    frame["doy_bin"] = (day_of_year // 15).astype(int)
    return frame


def _history_features(frame: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Past landslides in the same cell, counted strictly before each sample date.

    Using the catalogue total instead would hand the model the answer: a cell
    with many recorded landslides is exactly a cell where landslides happen.
    """
    frame = frame.copy()
    history = (
        events[["cell_id", "date_id"]]
        .sort_values(["cell_id", "date_id"])
        .reset_index(drop=True)
    )

    counts: dict[int, np.ndarray] = {
        cell_id: group["date_id"].to_numpy()
        for cell_id, group in history.groupby("cell_id", sort=False)
    }

    def prior_events(cell_id: int, date_id: int) -> int:
        dates = counts.get(cell_id)
        if dates is None:
            return 0
        return int(np.searchsorted(dates, date_id, side="left"))

    frame["hist_events_before"] = [
        prior_events(int(cell), int(date))
        for cell, date in zip(frame["cell_id"], frame["date_id"])
    ]
    frame["has_prior_event"] = (frame["hist_events_before"] > 0).astype(int)
    return frame


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _safe_ratio(numerator, denominator) -> pd.Series:
    numerator = pd.Series(numerator).astype("float64").reset_index(drop=True)
    denominator = pd.Series(denominator).astype("float64").reset_index(drop=True)
    ratio = numerator / denominator.replace(0, np.nan)
    return ratio.replace([np.inf, -np.inf], np.nan)


def _require_columns(frame: pd.DataFrame, columns, name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise KeyError(f"{name} is missing required columns: {missing}")


# River discharge is fetched and stored — it is part of the flash-flood picture
# and the dashboard reports it — but it is deliberately kept out of the model.
# Two reasons. It never reached the top fifteen features by SHAP, because GloFAS
# models river reaches and most landslide cells are headwater slopes where it
# reads near zero. And fetching it doubles the API cost, so once the budget is
# the binding constraint, half the sample would have it and half would not — and
# a feature whose presence correlates with fetch order is a leak, not a feature.
EXCLUDED_FEATURES = {"discharge", "discharge_ratio"}


def feature_columns(frame: pd.DataFrame) -> list[str]:
    """Model inputs — everything that is not an identifier or a label."""
    excluded = {
        "cell_id", "date_id", "label", "stratum", "event_id",
        "split", "region_block", "doy_bin", "date",
    } | EXCLUDED_FEATURES
    return [
        column for column in frame.columns
        if column not in excluded and pd.api.types.is_numeric_dtype(frame[column])
    ]
