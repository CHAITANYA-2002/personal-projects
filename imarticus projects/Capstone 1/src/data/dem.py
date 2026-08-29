"""Copernicus DEM GLO-30 — terrain for every grid cell.

The bounding box needs 442 one-degree tiles, about 17.5 GB of cloud-optimised
GeoTIFF served from S3 without authentication. Downloads are resumable: a tile
whose local size already matches the remote content-length is skipped, so an
interrupted run costs nothing to restart.

Terrain statistics are computed tile by tile rather than on a mosaic. A single
tile is 3601x3601 float32, roughly 50 MB in memory, and holds exactly 100 of
our 0.1-degree cells — so the whole arc is processed in bounded memory without
ever building a 5.7-billion-pixel array.

Slope needs care at these latitudes. Pixel spacing is constant in degrees but
not in metres: a degree of longitude shrinks by cos(latitude), which at 35N is
an 18% difference from the naive assumption. Getting this wrong tilts every
slope estimate in the western Himalaya relative to the north-east.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
import pandas as pd
import requests

from config import settings

log = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 600
_CHUNK_BYTES = 4 << 20
_METRES_PER_DEGREE = 111_320.0
_NODATA_SENTINELS = (-32767.0, -9999.0)


@dataclass(frozen=True)
class Tile:
    """One 1-degree Copernicus DEM tile."""

    lat: int   # south-west corner
    lon: int

    @property
    def name(self) -> str:
        ns = f"N{self.lat:02d}" if self.lat >= 0 else f"S{abs(self.lat):02d}"
        ew = f"E{self.lon:03d}" if self.lon >= 0 else f"W{abs(self.lon):03d}"
        return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"

    @property
    def url(self) -> str:
        return f"{settings.COPERNICUS_DEM_BUCKET}/{self.name}/{self.name}.tif"

    @property
    def path(self) -> Path:
        return settings.DEM_RAW_DIR / f"{self.name}.tif"


def tiles_for_study_area() -> list[Tile]:
    """Every tile touching the study bounding box."""
    lat_range = range(int(math.floor(settings.LAT_MIN)), int(math.ceil(settings.LAT_MAX)))
    lon_range = range(int(math.floor(settings.LON_MIN)), int(math.ceil(settings.LON_MAX)))
    return [Tile(lat, lon) for lat in lat_range for lon in lon_range]


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------

def download_tiles(
    tiles: Sequence[Tile] | None = None,
    workers: int = 6,
) -> dict[str, int]:
    """Download every missing tile. Returns a small summary dict."""
    tiles = list(tiles) if tiles is not None else tiles_for_study_area()
    settings.DEM_RAW_DIR.mkdir(parents=True, exist_ok=True)

    log.info("checking %d DEM tiles in %s", len(tiles), settings.DEM_RAW_DIR)

    summary = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0}
    session = requests.Session()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_one, tile, session): tile for tile in tiles}
        done = 0
        for future in as_completed(futures):
            tile = futures[future]
            done += 1
            try:
                status, size = future.result()
            except Exception as exc:
                log.error("tile %s failed: %s", tile.name, exc)
                summary["failed"] += 1
                continue
            summary[status] += 1
            summary["bytes"] += size
            if done % 25 == 0 or done == len(tiles):
                log.info(
                    "%d/%d tiles | %d downloaded, %d cached, %d failed | %.1f GB",
                    done, len(tiles), summary["downloaded"], summary["skipped"],
                    summary["failed"], summary["bytes"] / 1e9,
                )

    return summary


def _download_one(tile: Tile, session: requests.Session) -> tuple[str, int]:
    head = session.head(tile.url, timeout=60)
    head.raise_for_status()
    expected = int(head.headers.get("content-length", 0))

    if tile.path.exists() and expected and tile.path.stat().st_size == expected:
        return "skipped", expected

    partial = tile.path.with_suffix(".part")
    with session.get(tile.url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                handle.write(chunk)

    written = partial.stat().st_size
    if expected and written != expected:
        partial.unlink(missing_ok=True)
        raise IOError(f"{tile.name}: got {written} bytes, expected {expected}")

    partial.replace(tile.path)
    return "downloaded", written


def missing_tiles(tiles: Sequence[Tile] | None = None) -> list[Tile]:
    tiles = list(tiles) if tiles is not None else tiles_for_study_area()
    return [tile for tile in tiles if not tile.path.exists()]


# --------------------------------------------------------------------------
# terrain statistics
# --------------------------------------------------------------------------

def terrain_for_tile(tile: Tile) -> pd.DataFrame:
    """Per-cell terrain statistics for the 0.1-degree cells inside one tile."""
    import rasterio  # imported here so download-only runs need no GDAL

    if not tile.path.exists():
        raise FileNotFoundError(f"tile not downloaded: {tile.path}")

    with rasterio.open(tile.path) as src:
        elevation = src.read(1).astype("float32")
        transform = src.transform
        nodata = src.nodata

    elevation = _mask_nodata(elevation, nodata)

    res_lat_deg = abs(transform.e)
    res_lon_deg = abs(transform.a)
    origin_lat = transform.f          # north edge
    origin_lon = transform.c          # west edge

    centre_lat = tile.lat + 0.5
    slope_deg, aspect_deg = _slope_aspect(
        elevation, res_lat_deg, res_lon_deg, centre_lat
    )
    tri = _ruggedness(elevation)

    rows: list[dict] = []
    step = settings.GRID_DEG

    for cell_lat in _frange(tile.lat, tile.lat + 1, step):
        for cell_lon in _frange(tile.lon, tile.lon + 1, step):
            if not _cell_in_study_area(cell_lat, cell_lon):
                continue

            row0 = int(round((origin_lat - (cell_lat + step)) / res_lat_deg))
            row1 = int(round((origin_lat - cell_lat) / res_lat_deg))
            col0 = int(round((cell_lon - origin_lon) / res_lon_deg))
            col1 = int(round((cell_lon + step - origin_lon) / res_lon_deg))

            row0, row1 = max(0, row0), min(elevation.shape[0], row1)
            col0, col1 = max(0, col0), min(elevation.shape[1], col1)
            if row1 <= row0 or col1 <= col0:
                continue

            stats = _cell_stats(
                elevation[row0:row1, col0:col1],
                slope_deg[row0:row1, col0:col1],
                aspect_deg[row0:row1, col0:col1],
                tri[row0:row1, col0:col1],
            )
            if stats is None:
                continue

            stats["lat_c"] = round(cell_lat + step / 2, settings.GRID_PRECISION)
            stats["lon_c"] = round(cell_lon + step / 2, settings.GRID_PRECISION)
            rows.append(stats)

    return pd.DataFrame(rows)


def _mask_nodata(array: np.ndarray, nodata: float | None) -> np.ndarray:
    masked = array.copy()
    sentinels = set(_NODATA_SENTINELS)
    if nodata is not None:
        sentinels.add(float(nodata))
    for value in sentinels:
        masked[masked == value] = np.nan
    # Copernicus uses large negatives for voids; anything below the Dead Sea
    # in this study area is a void rather than real terrain.
    masked[masked < -500.0] = np.nan
    return masked


def _slope_aspect(
    elevation: np.ndarray,
    res_lat_deg: float,
    res_lon_deg: float,
    centre_lat: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and aspect in degrees clockwise from north."""
    spacing_y = res_lat_deg * _METRES_PER_DEGREE
    spacing_x = res_lon_deg * _METRES_PER_DEGREE * math.cos(math.radians(centre_lat))

    # np.gradient walks rows top-to-bottom, and rows run north-to-south, so the
    # row derivative is negated to give a true northward gradient.
    d_row, d_col = np.gradient(elevation, spacing_y, spacing_x)
    dz_dy = -d_row
    dz_dx = d_col

    magnitude = np.hypot(dz_dx, dz_dy)
    slope = np.degrees(np.arctan(magnitude))
    aspect = (np.degrees(np.arctan2(dz_dy, -dz_dx)) + 360.0) % 360.0
    return slope.astype("float32"), aspect.astype("float32")


def _ruggedness(elevation: np.ndarray) -> np.ndarray:
    """Terrain ruggedness: mean absolute elevation change to the four neighbours."""
    padded = np.pad(elevation, 1, mode="edge")
    diffs = [
        np.abs(elevation - padded[0:-2, 1:-1]),
        np.abs(elevation - padded[2:, 1:-1]),
        np.abs(elevation - padded[1:-1, 0:-2]),
        np.abs(elevation - padded[1:-1, 2:]),
    ]
    return np.nanmean(np.stack(diffs), axis=0).astype("float32")


def _cell_stats(
    elevation: np.ndarray,
    slope: np.ndarray,
    aspect: np.ndarray,
    tri: np.ndarray,
) -> dict | None:
    valid = np.isfinite(elevation)
    if valid.sum() < elevation.size * 0.5:
        return None   # more than half void — not trustworthy

    aspect_rad = np.radians(aspect[valid])
    weights = slope[valid]   # flat ground has no meaningful aspect
    weight_sum = float(np.nansum(weights))
    if weight_sum > 0:
        aspect_sin = float(np.nansum(np.sin(aspect_rad) * weights) / weight_sum)
        aspect_cos = float(np.nansum(np.cos(aspect_rad) * weights) / weight_sum)
    else:
        aspect_sin = aspect_cos = 0.0

    return {
        "elev_mean": round(float(np.nanmean(elevation)), 1),
        "elev_min": round(float(np.nanmin(elevation)), 1),
        "elev_max": round(float(np.nanmax(elevation)), 1),
        "elev_range": round(float(np.nanmax(elevation) - np.nanmin(elevation)), 1),
        "slope_mean": round(float(np.nanmean(slope)), 2),
        "slope_max": round(float(np.nanmax(slope)), 2),
        "slope_std": round(float(np.nanstd(slope)), 2),
        "aspect_sin": round(aspect_sin, 4),
        "aspect_cos": round(aspect_cos, 4),
        "tri": round(float(np.nanmean(tri)), 2),
    }


def _cell_in_study_area(cell_lat: float, cell_lon: float) -> bool:
    return (
        settings.LAT_MIN <= cell_lat < settings.LAT_MAX
        and settings.LON_MIN <= cell_lon < settings.LON_MAX
    )


def _frange(start: float, stop: float, step: float) -> Iterator[float]:
    steps = int(round((stop - start) / step))
    for index in range(steps):
        yield round(start + index * step, 6)
