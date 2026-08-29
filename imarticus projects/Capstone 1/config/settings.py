"""Central configuration for Slopewatch.

Every path, bound and magic number the pipeline depends on lives here so that
nothing downstream hardcodes a value. Credentials come from the environment
via a .env file that is never committed.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
REFERENCE_DIR = DATA_DIR / "reference"

WEATHER_RAW_DIR = RAW_DIR / "weather"
DEM_RAW_DIR = RAW_DIR / "dem"
OSM_RAW_DIR = RAW_DIR / "osm"

SQL_DIR = PROJECT_ROOT / "sql"
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"

ALL_DIRS = (
    RAW_DIR, INTERIM_DIR, PROCESSED_DIR, REFERENCE_DIR,
    WEATHER_RAW_DIR, DEM_RAW_DIR, OSM_RAW_DIR,
    MODELS_DIR, LOGS_DIR,
)


def ensure_dirs() -> None:
    """Create every directory the pipeline writes into. Safe to call repeatedly."""
    for directory in ALL_DIRS:
        directory.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# study area
# --------------------------------------------------------------------------
# Himalayan arc plus the North Eastern Region. Deliberately a bounding box and
# not a list of state names: the source catalogue spells states inconsistently
# ('Nagaland' and 'Nāgāland' are separate values), so name matching silently
# drops events. Non-hilly cells inside the box are removed later by the slope
# mask, which is a more reliable filter than any spelling.
LAT_MIN, LAT_MAX = 21.5, 37.5
LON_MIN, LON_MAX = 72.0, 97.5

GRID_DEG = 0.1          # matches ERA5-Land native resolution (~11 km)
GRID_PRECISION = 3      # decimals kept on cell centroids

# Cells flatter than this are dropped from the modelling population. A landslide
# needs a slope; keeping floodplain cells only dilutes the negative pool.
MIN_SLOPE_DEG = 5.0

DATE_START = date(2007, 1, 1)
# The catalogue runs to 2017-09-28, but 2017 is incomplete (August is missing
# entirely) and every 2017 event in this box is outside India. Capping at the
# last full year avoids a partial season skewing the monsoon signal.
DATE_END = date(2016, 12, 31)

MONSOON_MONTHS = (6, 7, 8, 9)

# The bounding box spans the whole Himalayan orogen, not just India. Training
# on the full arc nearly doubles the sample (1,781 events against 1,017) and the
# added events share the same geology and monsoon regime, so the physics the
# model learns transfers. Reporting is always done on the evaluation country, and
# leave-one-country-out becomes a genuine test of spatial generalisation.
# Set INCLUDE_COUNTRIES = ("India",) to restrict training to India alone.
INCLUDE_COUNTRIES = (
    "India", "Nepal", "Pakistan", "Bhutan",
    "Bangladesh", "Myanmar [Burma]", "China",
)
EVALUATION_COUNTRY = "India"

# Reference only — for reporting and sanity checks, never for filtering.
NER_STATES = (
    "Assam", "Manipur", "Sikkim", "Mizoram",
    "Nagaland", "Arunachal Pradesh", "Meghalaya", "Tripura",
)
WEST_HIMALAYAN_STATES = ("Kashmir", "Uttarakhand", "Himachal Pradesh")


# --------------------------------------------------------------------------
# grid identity
# --------------------------------------------------------------------------
LON_INDEX_SPAN = 1000  # keeps cell_id = lat_idx * span + lon_idx collision-free

N_LAT_CELLS = round((LAT_MAX - LAT_MIN) / GRID_DEG)
N_LON_CELLS = round((LON_MAX - LON_MIN) / GRID_DEG)

if N_LON_CELLS >= LON_INDEX_SPAN:  # pragma: no cover - guards a config mistake
    raise ValueError(
        f"LON_INDEX_SPAN ({LON_INDEX_SPAN}) must exceed the number of longitude "
        f"cells ({N_LON_CELLS}) or cell ids will collide."
    )


# --------------------------------------------------------------------------
# sampling design
# --------------------------------------------------------------------------
# 1:6 rather than a larger ratio. The binding constraint is not statistics but
# the weather API: every sample needs its own multi-week window, and the free
# allowance is 10,000 calls a day. A fully-covered 1:6 sample beats a 1:15 one
# where most rows never received weather, and residual imbalance is handled by
# class weighting rather than by drawing more negatives.
NEGATIVES_PER_POSITIVE = 6
STRATUM_WEIGHTS = {          # must sum to 1.0
    "temporal": 0.50,        # same cell, different (season-matched) date
    "spatial": 0.30,         # different cell, same date
    "background": 0.20,      # random hill cell and date
}

# A candidate negative this close to a recorded event is discarded rather than
# labelled zero — the catalogue records reported landslides, and unreported ones
# are common in remote terrain.
EXCLUSION_RADIUS_KM = 15.0
EXCLUSION_DAYS = 2

# Feature window pulled around each sampled date. 45 days keeps the 30-day
# antecedent accumulation that the landslide literature relies on while costing
# a quarter less API budget than 60 — the 60-day feature is the first thing
# worth dropping when the constraint is calls rather than science.
FEATURE_WINDOW_DAYS = 45

# Blocked, never random. Spatial blocking is applied on top of this split.
SPLIT_TRAIN_END = date(2013, 12, 31)
SPLIT_VAL_END = date(2014, 12, 31)


# --------------------------------------------------------------------------
# external sources
# --------------------------------------------------------------------------
GLC_CSV_URL = (
    "https://data.nasa.gov/docs/legacy/Global_Landslide_Catalog_Export/"
    "Global_Landslide_Catalog_Export_rows.csv"
)

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPENMETEO_FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

OPENMETEO_DAILY_VARS = (
    "precipitation_sum",
    "rain_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_7_to_28cm_mean",
    "soil_moisture_28_to_100cm_mean",
    "et0_fao_evapotranspiration",
    "wind_speed_10m_max",
)

COPERNICUS_DEM_BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"
GEOFABRIK_EXTRACTS = (
    "https://download.geofabrik.de/asia/india/north-eastern-zone-latest-free.shp.zip",
    "https://download.geofabrik.de/asia/india/northern-zone-latest-free.shp.zip",
)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


OPENMETEO_DAILY_BUDGET = _int_env("OPENMETEO_DAILY_BUDGET", 9000)
OPENMETEO_BATCH_SIZE = _int_env("OPENMETEO_BATCH_SIZE", 8)
OPENMETEO_SLEEP_SECONDS = _float_env("OPENMETEO_SLEEP_SECONDS", 0.4)


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _int_env("MYSQL_PORT", 3306)
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "slopewatch")
MYSQL_USER = os.getenv("MYSQL_USER", "slopewatch")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")


def database_url(include_database: bool = True) -> str:
    """SQLAlchemy URL for the warehouse.

    Deployment portability depends on this being the only place a connection
    string is assembled — moving to a managed MySQL later is an .env change.
    """
    if not MYSQL_PASSWORD:
        raise RuntimeError(
            "MYSQL_PASSWORD is not set. Copy .env.example to .env and fill it in."
        )
    from urllib.parse import quote_plus

    auth = f"{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
    target = f"{MYSQL_HOST}:{MYSQL_PORT}"
    suffix = f"/{MYSQL_DATABASE}" if include_database else ""
    return f"mysql+pymysql://{auth}@{target}{suffix}?charset=utf8mb4"
