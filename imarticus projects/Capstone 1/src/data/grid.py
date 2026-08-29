"""The 0.1-degree analysis grid and the date dimension.

The grid resolution is not a free choice. The landslide catalogue records most
event positions to within 5-25 km, so a finer lattice would produce confident
predictions the labels cannot support. 0.1 degrees is roughly 11 km and also
matches ERA5-Land's native resolution, which means one grid cell maps to one
weather pixel with no resampling.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config import settings

log = logging.getLogger(__name__)

_SEASONS = {
    (12, 1, 2): "winter",
    (3, 4, 5): "pre-monsoon",
    (6, 7, 8, 9): "monsoon",
    (10, 11): "post-monsoon",
}


# --------------------------------------------------------------------------
# cell identity
# --------------------------------------------------------------------------

def to_indices(lat: float, lon: float) -> tuple[int, int]:
    """Grid indices for a coordinate. Does not check bounds."""
    lat_idx = int(np.floor((lat - settings.LAT_MIN) / settings.GRID_DEG))
    lon_idx = int(np.floor((lon - settings.LON_MIN) / settings.GRID_DEG))
    return lat_idx, lon_idx


def cell_id_from_indices(lat_idx: int, lon_idx: int) -> int:
    return lat_idx * settings.LON_INDEX_SPAN + lon_idx


def cell_id_from_latlon(lat: float, lon: float) -> int | None:
    """Cell id for a coordinate, or None if it falls outside the study area."""
    if not in_study_area(lat, lon):
        return None
    lat_idx, lon_idx = to_indices(lat, lon)
    return cell_id_from_indices(lat_idx, lon_idx)


def in_study_area(lat: float, lon: float) -> bool:
    return (
        settings.LAT_MIN <= lat < settings.LAT_MAX
        and settings.LON_MIN <= lon < settings.LON_MAX
    )


def centroid_from_cell_id(cell_id: int) -> tuple[float, float]:
    """Inverse of cell_id_from_latlon — returns the cell centre."""
    lat_idx, lon_idx = divmod(cell_id, settings.LON_INDEX_SPAN)
    half = settings.GRID_DEG / 2
    lat = settings.LAT_MIN + lat_idx * settings.GRID_DEG + half
    lon = settings.LON_MIN + lon_idx * settings.GRID_DEG + half
    return round(lat, settings.GRID_PRECISION), round(lon, settings.GRID_PRECISION)


def assign_cells(frame: pd.DataFrame, lat_col: str, lon_col: str) -> pd.Series:
    """Vectorised cell assignment. Returns a nullable integer series."""
    lat = frame[lat_col].astype(float)
    lon = frame[lon_col].astype(float)

    inside = (
        lat.between(settings.LAT_MIN, settings.LAT_MAX, inclusive="left")
        & lon.between(settings.LON_MIN, settings.LON_MAX, inclusive="left")
    )

    lat_idx = np.floor((lat - settings.LAT_MIN) / settings.GRID_DEG)
    lon_idx = np.floor((lon - settings.LON_MIN) / settings.GRID_DEG)
    cell = lat_idx * settings.LON_INDEX_SPAN + lon_idx

    return cell.where(inside).astype("Int64")


# --------------------------------------------------------------------------
# dimension builders
# --------------------------------------------------------------------------

def build_grid_frame() -> pd.DataFrame:
    """Every cell in the study bounding box.

    Terrain and context columns are left null here; the DEM and OSM phases fill
    them in and set is_hill.
    """
    lat_indices = np.arange(settings.N_LAT_CELLS, dtype=np.int64)
    lon_indices = np.arange(settings.N_LON_CELLS, dtype=np.int64)
    lat_grid, lon_grid = np.meshgrid(lat_indices, lon_indices, indexing="ij")

    lat_flat = lat_grid.ravel()
    lon_flat = lon_grid.ravel()
    half = settings.GRID_DEG / 2

    frame = pd.DataFrame(
        {
            "cell_id": lat_flat * settings.LON_INDEX_SPAN + lon_flat,
            "lat_idx": lat_flat,
            "lon_idx": lon_flat,
            "lat_c": np.round(
                settings.LAT_MIN + lat_flat * settings.GRID_DEG + half,
                settings.GRID_PRECISION,
            ),
            "lon_c": np.round(
                settings.LON_MIN + lon_flat * settings.GRID_DEG + half,
                settings.GRID_PRECISION,
            ),
        }
    )
    frame["is_hill"] = 0
    frame["in_study_area"] = 1

    log.info(
        "built grid: %d cells (%d lat x %d lon) covering %.1f-%.1fN, %.1f-%.1fE",
        len(frame), settings.N_LAT_CELLS, settings.N_LON_CELLS,
        settings.LAT_MIN, settings.LAT_MAX, settings.LON_MIN, settings.LON_MAX,
    )
    return frame


def build_date_frame(
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """The date dimension across the study window."""
    start = start or settings.DATE_START
    end = end or settings.DATE_END
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    days = pd.date_range(start, end, freq="D")
    frame = pd.DataFrame({"full_date": days})

    frame["date_id"] = (
        frame["full_date"].dt.strftime("%Y%m%d").astype(np.int64)
    )
    frame["year"] = frame["full_date"].dt.year
    frame["quarter"] = frame["full_date"].dt.quarter
    frame["month"] = frame["full_date"].dt.month
    frame["month_name"] = frame["full_date"].dt.strftime("%B")
    frame["day"] = frame["full_date"].dt.day
    frame["day_of_year"] = frame["full_date"].dt.dayofyear
    frame["week_of_year"] = frame["full_date"].dt.isocalendar().week.astype(np.int64)
    frame["season"] = frame["month"].map(_season_for_month)
    frame["is_monsoon"] = frame["month"].isin(settings.MONSOON_MONTHS).astype(int)

    frame["full_date"] = frame["full_date"].dt.date
    ordered = [
        "date_id", "full_date", "year", "quarter", "month", "month_name",
        "day", "day_of_year", "week_of_year", "season", "is_monsoon",
    ]

    log.info("built date dimension: %d days from %s to %s", len(frame), start, end)
    return frame[ordered]


def _season_for_month(month: int) -> str:
    for months, name in _SEASONS.items():
        if month in months:
            return name
    raise ValueError(f"month out of range: {month}")


def date_to_id(value: date) -> int:
    return value.year * 10_000 + value.month * 100 + value.day


def id_to_date(date_id: int) -> date:
    year, remainder = divmod(int(date_id), 10_000)
    month, day = divmod(remainder, 100)
    return date(year, month, day)


def date_window(centre: date, days_before: int) -> tuple[date, date]:
    """Inclusive window ending on `centre`, used for feature extraction."""
    return centre - timedelta(days=days_before - 1), centre
