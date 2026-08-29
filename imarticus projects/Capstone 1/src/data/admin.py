"""Administrative boundaries — the unit decisions are actually made in.

A grid cell is a modelling convenience. Nobody dispatches a team to cell 92070;
they dispatch to Rudraprayag district. Every output that reaches an authority
therefore has to carry a district name, and that means a spatial join against
real boundaries.

Neither OSM's free bundle nor the DEM carries them — OSM's places layer holds 33
counties and two regions for the whole extract, which is not usable. geoBoundaries
publishes open state (ADM1) and district (ADM2) polygons for India with no
authentication, so those are used.

The simplified geometries are deliberate. At 0.1 degrees a cell is about 11 km
across, so boundary detail finer than that cannot change which cell falls where,
and the full-resolution file is an order of magnitude larger for no gain.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

from config import settings
from src.data import landslides

log = logging.getLogger(__name__)

_API = "https://www.geoboundaries.org/api/current/gbOpen/IND"
_TIMEOUT = 300

LEVELS = {
    "ADM1": "state_name",
    "ADM2": "district_name",
}


def boundary_path(level: str) -> Path:
    return settings.REFERENCE_DIR / f"india_{level.lower()}.geojson"


def download(level: str, simplified: bool = True) -> Path:
    """Fetch one admin level to data/reference. Skips if already present."""
    target = boundary_path(level)
    if target.exists() and target.stat().st_size > 1000:
        log.info("%s already downloaded", target.name)
        return target

    settings.REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    meta = requests.get(f"{_API}/{level}/", timeout=_TIMEOUT)
    meta.raise_for_status()
    payload = meta.json()
    if isinstance(payload, list):
        payload = payload[0]

    key = "simplifiedGeometryGeoJSON" if simplified else "gjDownloadURL"
    url = payload.get(key) or payload["gjDownloadURL"]

    log.info("downloading %s boundaries (%s)", level,
             payload.get("boundaryYearRepresented", "year unknown"))
    response = requests.get(url, timeout=_TIMEOUT)
    response.raise_for_status()

    partial = target.with_suffix(".part")
    partial.write_bytes(response.content)
    partial.replace(target)

    log.info("saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target


def label_cells(cells: pd.DataFrame) -> pd.DataFrame:
    """Attach state and district names to grid cells by point-in-polygon.

    Cells are represented by their centroid. A cell straddling a boundary is
    assigned to whichever district holds its centre, which is the same
    convention the risk map uses, so the two never disagree.
    """
    import geopandas as gpd

    points = gpd.GeoDataFrame(
        cells[["cell_id"]].copy(),
        geometry=gpd.points_from_xy(cells["lon_c"], cells["lat_c"]),
        crs="EPSG:4326",
    )

    for level, column in LEVELS.items():
        path = download(level)
        boundaries = gpd.read_file(path)

        name_column = _name_column(boundaries)
        if name_column is None:
            log.warning("no name column found in %s — skipping", path.name)
            continue

        boundaries = boundaries[[name_column, "geometry"]].rename(
            columns={name_column: column}
        )
        boundaries = boundaries[boundaries.geometry.notna()]

        # geoBoundaries carries diacritics ("Uttarakhand" appears as
        # "Uttarakhand" with a macron), and the landslide catalogue is folded to
        # ASCII. Leaving them unfolded means the two never join and every
        # state-level report silently splits in two.
        boundaries[column] = boundaries[column].map(landslides.normalise_state)

        joined = gpd.sjoin(
            points, boundaries, how="left", predicate="within"
        )
        # A centroid on a shared edge can match two polygons; keep the first so
        # every cell resolves to exactly one district.
        joined = joined[~joined.index.duplicated(keep="first")]

        points[column] = joined[column].to_numpy()
        matched = int(points[column].notna().sum())
        log.info("%s: %d of %d cells labelled (%.1f%%)",
                 column, matched, len(points), 100 * matched / len(points))

    columns = ["cell_id"] + [c for c in LEVELS.values() if c in points.columns]
    return pd.DataFrame(points[columns])


def _name_column(frame) -> str | None:
    """geoBoundaries uses shapeName; other sources vary."""
    for candidate in ("shapeName", "NAME", "name", "ADM1_EN", "ADM2_EN"):
        if candidate in frame.columns:
            return candidate
    return None
