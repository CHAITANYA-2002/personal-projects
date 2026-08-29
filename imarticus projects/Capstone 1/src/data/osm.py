"""OpenStreetMap exposure — who and what is downhill of a risky cell.

This is the layer that separates a decision-support system from a model. A
probability tells an officer that a slope may fail; exposure tells them that
failure severs a district road, cuts off fourteen villages and isolates one
health centre. Only the second sentence dispatches a team.

Geofabrik publishes India as pre-separated thematic shapefiles, which is why
this module reads shapefiles rather than parsing raw OSM. Roads, settlements
and points of interest are each joined to the 0.1-degree grid once and the
result is stored, because the joins are expensive and the answer never changes
between scoring runs.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from config import settings

log = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 900
_CHUNK_BYTES = 4 << 20

# Layers inside a Geofabrik "free" shapefile bundle that we actually use.
ROAD_LAYER = "gis_osm_roads_free_1"
PLACE_LAYER = "gis_osm_places_free_1"
POI_LAYER = "gis_osm_pois_free_1"

# Road classes worth counting separately: losing a track inconveniences a
# hamlet, losing a trunk road isolates a district.
PRIMARY_ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")

SETTLEMENT_TYPES = ("city", "town", "village", "hamlet", "suburb")

# Rough population defaults where OSM has no figure. Deliberately conservative
# and clearly labelled as estimates — they rank cells, they do not census them.
PLACE_POPULATION_DEFAULTS = {
    "city": 100_000,
    "town": 20_000,
    "suburb": 10_000,
    "village": 2_000,
    "hamlet": 300,
}

SCHOOL_TYPES = ("school", "college", "university", "kindergarten")
HEALTH_TYPES = ("hospital", "clinic", "doctors", "pharmacy")


def extract_dir(url: str) -> Path:
    return settings.OSM_RAW_DIR / Path(url).name.replace(".zip", "")


def download_extracts(urls: tuple[str, ...] | None = None) -> list[Path]:
    """Fetch and unzip the Geofabrik bundles. Skips work already done."""
    urls = urls or settings.GEOFABRIK_EXTRACTS
    settings.OSM_RAW_DIR.mkdir(parents=True, exist_ok=True)

    directories: list[Path] = []
    session = requests.Session()

    for url in urls:
        archive = settings.OSM_RAW_DIR / Path(url).name
        target = extract_dir(url)

        if target.exists() and any(target.glob("*.shp")):
            log.info("already extracted: %s", target.name)
            directories.append(target)
            continue

        if not archive.exists():
            log.info("downloading %s", archive.name)
            with session.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                partial = archive.with_suffix(".part")
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                        handle.write(chunk)
                partial.replace(archive)
            log.info("saved %s (%.0f MB)", archive.name,
                     archive.stat().st_size / 1e6)

        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
        log.info("extracted %s", target.name)
        directories.append(target)

    return directories


def _read_layer(directories: list[Path], layer: str):
    """Concatenate one layer across every extract."""
    import geopandas as gpd

    frames = []
    for directory in directories:
        matches = list(directory.rglob(f"{layer}.shp"))
        if not matches:
            log.warning("layer %s not found in %s", layer, directory.name)
            continue
        frame = gpd.read_file(matches[0])
        frames.append(frame)
        log.info("read %s from %s: %d features", layer, directory.name, len(frame))

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _cell_index(latitudes, longitudes) -> pd.Series:
    """Vectorised grid assignment for arbitrary coordinates."""
    latitudes = np.asarray(latitudes, dtype=float)
    longitudes = np.asarray(longitudes, dtype=float)

    inside = (
        (latitudes >= settings.LAT_MIN) & (latitudes < settings.LAT_MAX)
        & (longitudes >= settings.LON_MIN) & (longitudes < settings.LON_MAX)
    )
    lat_idx = np.floor((latitudes - settings.LAT_MIN) / settings.GRID_DEG)
    lon_idx = np.floor((longitudes - settings.LON_MIN) / settings.GRID_DEG)
    cell = lat_idx * settings.LON_INDEX_SPAN + lon_idx
    return pd.Series(np.where(inside, cell, np.nan)).astype("Int64")


def roads_per_cell(directories: list[Path]) -> pd.DataFrame:
    """Road length per cell, split into all roads and the primary network.

    Assigning a whole road to whichever cell holds its midpoint would be wrong:
    a 40 km highway crossing six cells would burden one and spare five. The
    geometrically exact fix is to clip every line against the grid, but
    ``overlay`` on millions of OSM features takes hours.

    Instead the roads are decomposed into their constituent vertex-to-vertex
    segments, and each segment is attributed to the cell containing its
    midpoint. OSM segments are tens of metres long against 11 km cells, so the
    error is negligible, and the whole operation is vectorised over a coordinate
    array rather than looping over geometries.
    """
    import shapely

    roads = _read_layer(directories, ROAD_LAYER)
    if roads is None or roads.empty:
        return pd.DataFrame(columns=["cell_id", "road_km_total", "road_km_primary"])

    roads = roads[roads.geometry.notna()]
    log.info("decomposing %d road features into segments", len(roads))

    coords, feature_index = shapely.get_coordinates(
        roads.geometry.values, return_index=True
    )

    # A segment spans consecutive coordinates belonging to the same feature.
    same_feature = feature_index[:-1] == feature_index[1:]
    start = coords[:-1][same_feature]
    end = coords[1:][same_feature]
    owner = feature_index[:-1][same_feature]

    lengths_km = _segment_lengths_km(start, end)
    mid_lon = (start[:, 0] + end[:, 0]) / 2.0
    mid_lat = (start[:, 1] + end[:, 1]) / 2.0

    segments = pd.DataFrame({
        "cell_id": _cell_index(mid_lat, mid_lon),
        "length_km": lengths_km,
        "fclass": roads["fclass"].to_numpy()[owner],
    }).dropna(subset=["cell_id"])
    segments["cell_id"] = segments["cell_id"].astype("int64")

    log.info("%d segments assigned to %d cells",
             len(segments), segments["cell_id"].nunique())

    total = segments.groupby("cell_id")["length_km"].sum().rename("road_km_total")
    primary = (
        segments[segments["fclass"].isin(PRIMARY_ROAD_CLASSES)]
        .groupby("cell_id")["length_km"].sum().rename("road_km_primary")
    )

    frame = pd.concat([total, primary], axis=1).fillna(0.0).reset_index()
    frame[["road_km_total", "road_km_primary"]] = frame[
        ["road_km_total", "road_km_primary"]
    ].round(3)
    return frame


def _segment_lengths_km(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Great-circle length of each segment.

    Equirectangular approximation, which is accurate to well under a percent at
    segment scale and far cheaper than a full haversine over tens of millions of
    segments. The cos(latitude) term is what keeps longitude honest.
    """
    mean_lat = np.radians((start[:, 1] + end[:, 1]) / 2.0)
    d_lon = np.radians(end[:, 0] - start[:, 0]) * np.cos(mean_lat)
    d_lat = np.radians(end[:, 1] - start[:, 1])
    return 6371.0 * np.hypot(d_lon, d_lat)


def settlements_per_cell(directories: list[Path]) -> pd.DataFrame:
    """Settlement counts and an estimated population per cell."""
    places = _read_layer(directories, PLACE_LAYER)
    if places is None or places.empty:
        return pd.DataFrame(columns=["cell_id", "settlements", "est_population"])

    places = places[places["fclass"].isin(SETTLEMENT_TYPES)].copy()
    places = places[places.geometry.notna()]
    centroids = places.geometry.representative_point()
    places["cell_id"] = _cell_index(centroids.y.values, centroids.x.values).values
    places = places.dropna(subset=["cell_id"])

    population = _reported_population(places)
    fallback = places["fclass"].map(PLACE_POPULATION_DEFAULTS).fillna(500)
    places["est_population"] = population.fillna(fallback).clip(lower=0)

    grouped = (
        places.groupby("cell_id")
        .agg(settlements=("fclass", "size"),
             est_population=("est_population", "sum"))
        .reset_index()
    )
    grouped["cell_id"] = grouped["cell_id"].astype("int64")
    grouped["est_population"] = grouped["est_population"].round().astype("int64")
    return grouped


def facilities_per_cell(directories: list[Path]) -> pd.DataFrame:
    """Schools and health facilities per cell."""
    pois = _read_layer(directories, POI_LAYER)
    if pois is None or pois.empty:
        return pd.DataFrame(columns=["cell_id", "schools", "health_facilities"])

    pois = pois[pois.geometry.notna()].copy()
    centroids = pois.geometry.representative_point()
    pois["cell_id"] = _cell_index(centroids.y.values, centroids.x.values).values
    pois = pois.dropna(subset=["cell_id"])

    pois["is_school"] = pois["fclass"].isin(SCHOOL_TYPES).astype(int)
    pois["is_health"] = pois["fclass"].isin(HEALTH_TYPES).astype(int)

    grouped = (
        pois.groupby("cell_id")
        .agg(schools=("is_school", "sum"),
             health_facilities=("is_health", "sum"))
        .reset_index()
    )
    grouped["cell_id"] = grouped["cell_id"].astype("int64")
    return grouped


def _reported_population(places) -> pd.Series:
    """OSM population, with a stored zero treated as absent.

    Most Indian villages in the extract carry population="0" rather than no tag
    at all. Read literally that is a settlement with nobody in it, which zeroes
    its exposure weight and drops it out of every priority ranking — the small
    remote places the system exists to protect are exactly the ones this hides.
    """
    values = pd.to_numeric(places.get("population"), errors="coerce")
    return values.where(values > 0)


def settlement_records(directories: list[Path]) -> pd.DataFrame:
    """Named settlements, one row each, for dim_settlement.

    The aggregate counts in fact_exposure answer "how many villages", which is
    enough to rank a cell. They are not enough to act on it: a team is sent to
    Palampur, not to "one of forty settlements in cell 107043". Keeping the names
    is what turns the exposure number back into somewhere on a map.
    """
    places = _read_layer(directories, PLACE_LAYER)
    if places is None or places.empty:
        return pd.DataFrame()

    places = places[places["fclass"].isin(SETTLEMENT_TYPES)].copy()
    places = places[places.geometry.notna()]

    points = places.geometry.representative_point()
    places["latitude"] = points.y.round(6).to_numpy()
    places["longitude"] = points.x.round(6).to_numpy()
    places["cell_id"] = _cell_index(places["latitude"], places["longitude"]).values
    places = places.dropna(subset=["cell_id"])

    population = _reported_population(places)
    fallback = places["fclass"].map(PLACE_POPULATION_DEFAULTS).fillna(500)

    frame = pd.DataFrame({
        "osm_id": places["osm_id"].astype(str).str.slice(0, 32),
        "place_name": places["name"].astype(str).str.slice(0, 255),
        "place_type": places["fclass"].astype(str).str.slice(0, 32),
        "latitude": places["latitude"].to_numpy(),
        "longitude": places["longitude"].to_numpy(),
        "cell_id": places["cell_id"].astype("int64").to_numpy(),
        "est_population": population.fillna(fallback).clip(lower=0)
                                    .round().astype("int64").to_numpy(),
    })

    frame = frame[frame["place_name"].str.strip().ne("")]
    frame = frame[frame["place_name"].ne("nan")]
    frame = frame.drop_duplicates(subset="osm_id", keep="first")

    log.info("%d named settlements across %d cells",
             len(frame), frame["cell_id"].nunique())
    return frame.reset_index(drop=True)


def build_exposure(directories: list[Path]) -> pd.DataFrame:
    """Every exposure measure, one row per cell."""
    roads = roads_per_cell(directories)
    settlements = settlements_per_cell(directories)
    facilities = facilities_per_cell(directories)

    frame = roads
    for other in (settlements, facilities):
        if other.empty:
            continue
        frame = frame.merge(other, on="cell_id", how="outer")

    numeric = [column for column in frame.columns if column != "cell_id"]
    frame[numeric] = frame[numeric].fillna(0)
    frame["bridges"] = 0   # Geofabrik's free bundle carries no bridge layer

    log.info("exposure built for %d cells", len(frame))
    return frame
