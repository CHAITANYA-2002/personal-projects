"""NASA Global Landslide Catalog — the ground truth.

Two upstream quirks drive most of this module.

First, state names carry inconsistent diacritics: 'Nagaland' and 'Nāgāland' are
stored as separate values, as are 'Arunachal Pradesh' / 'Arunāchal Pradesh' and
'Meghalaya' / 'Meghālaya'. Grouping on the raw column silently splits and
undercounts a state by more than half. Everything downstream groups on the
normalised column instead, and the study area is selected by bounding box, not
by name.

Second, the download URL answers with a 302 to a signed S3 object, so redirects
must be followed.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from config import settings
from src.data import grid

log = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 300
_CHUNK_BYTES = 1 << 20

# location_accuracy arrives as '5km', '25km', 'exact', 'unknown'.
_ACCURACY_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*km$", re.IGNORECASE)
_EXACT_ACCURACY_KM = 0.0

_SOURCE_COLUMNS = [
    "event_id", "event_date", "event_title", "source_name",
    "country_name", "admin_division_name",
    "landslide_category", "landslide_trigger", "landslide_size",
    "location_accuracy", "fatality_count", "injury_count",
    "latitude", "longitude",
]


def catalog_path() -> Path:
    return settings.RAW_DIR / "global_landslide_catalog.csv"


def download(force: bool = False) -> Path:
    """Fetch the catalogue CSV to data/raw. Skips if already present."""
    target = catalog_path()
    if target.exists() and not force:
        log.info("catalogue already downloaded: %s", target.name)
        return target

    settings.ensure_dirs()
    log.info("downloading catalogue from NASA...")
    try:
        with requests.get(
            settings.GLC_CSV_URL, stream=True, timeout=_DOWNLOAD_TIMEOUT,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            temp = target.with_suffix(".part")
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                    handle.write(chunk)
            temp.replace(target)
    except requests.RequestException as exc:
        raise RuntimeError(
            "Could not download the landslide catalogue. Check connectivity; "
            "the URL redirects to a signed S3 object and needs redirects enabled."
        ) from exc

    log.info("saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target


def normalise_state(value: object) -> str | None:
    """Fold diacritics and casing so state names group correctly."""
    if not isinstance(value, str) or not value.strip():
        return None
    folded = unicodedata.normalize("NFKD", value)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    cleaned = " ".join(ascii_only.split())
    return cleaned.title() or None


def parse_accuracy_km(value: object) -> float | None:
    """Convert location_accuracy to kilometres. 'exact' is 0, 'unknown' is null."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text == "exact":
        return _EXACT_ACCURACY_KM
    match = _ACCURACY_PATTERN.match(text)
    return float(match.group(1)) if match else None


def load_raw() -> pd.DataFrame:
    """Read the catalogue as-is, with no filtering."""
    path = catalog_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run download() first."
        )
    frame = pd.read_csv(
        path,
        usecols=_SOURCE_COLUMNS,
        encoding="utf-8",
        encoding_errors="replace",
        low_memory=False,
    )
    log.info("read %d catalogue rows", len(frame))
    return frame


def prepare_events() -> pd.DataFrame:
    """Catalogue rows reduced to study-area events, ready for fact_landslide."""
    frame = load_raw()

    frame = frame.dropna(subset=["latitude", "longitude", "event_date"])
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame = frame.dropna(subset=["latitude", "longitude"])

    # Catalogue timestamps are US-style MM/DD/YYYY with a time component we
    # discard: a landslide's reported hour is far less reliable than its date.
    parsed = pd.to_datetime(
        frame["event_date"], format="%m/%d/%Y %I:%M:%S %p", errors="coerce"
    )
    fallback = pd.to_datetime(frame["event_date"], errors="coerce", format="mixed")
    frame["event_date"] = parsed.fillna(fallback)
    frame = frame.dropna(subset=["event_date"])

    before_area = len(frame)
    frame["cell_id"] = grid.assign_cells(frame, "latitude", "longitude")
    frame = frame.dropna(subset=["cell_id"])
    log.info(
        "study area filter: %d of %d events fall inside the bounding box",
        len(frame), before_area,
    )

    in_window = frame["event_date"].dt.date.between(
        settings.DATE_START, settings.DATE_END
    )
    dropped = int((~in_window).sum())
    frame = frame[in_window]
    if dropped:
        log.info("dropped %d events outside %s..%s",
                 dropped, settings.DATE_START, settings.DATE_END)

    # The bounding box covers the whole Himalayan arc, so Nepal, Pakistan,
    # Bhutan and others appear alongside India. Which of them to train on is a
    # deliberate configuration choice, not an accident of the geometry.
    before_country = len(frame)
    frame = frame[frame["country_name"].isin(settings.INCLUDE_COUNTRIES)]
    log.info(
        "country filter: kept %d of %d events across %s",
        len(frame), before_country, ", ".join(settings.INCLUDE_COUNTRIES),
    )

    frame["state_name_norm"] = frame["admin_division_name"].map(normalise_state)
    frame["loc_accuracy_km"] = frame["location_accuracy"].map(parse_accuracy_km)
    frame["date_id"] = frame["event_date"].dt.strftime("%Y%m%d").astype("int64")
    frame["cell_id"] = frame["cell_id"].astype("int64")

    out = pd.DataFrame(
        {
            "event_id": frame["event_id"].astype("int64"),
            "cell_id": frame["cell_id"],
            "date_id": frame["date_id"],
            "event_date": frame["event_date"].dt.date,
            "latitude": frame["latitude"].round(6),
            "longitude": frame["longitude"].round(6),
            "country_name": frame["country_name"],
            "state_name": frame["admin_division_name"],
            "state_name_norm": frame["state_name_norm"],
            "landslide_category": frame["landslide_category"],
            "landslide_trigger": frame["landslide_trigger"],
            "landslide_size": frame["landslide_size"],
            "location_accuracy": frame["location_accuracy"],
            "loc_accuracy_km": frame["loc_accuracy_km"],
            "fatality_count": pd.to_numeric(
                frame["fatality_count"], errors="coerce"
            ).fillna(0).clip(lower=0).astype("int64"),
            "injury_count": pd.to_numeric(
                frame["injury_count"], errors="coerce"
            ).fillna(0).clip(lower=0).astype("int64"),
            "source_name": frame["source_name"].astype(str).str.slice(0, 128),
            "event_title": frame["event_title"].astype(str).str.slice(0, 512),
        }
    )

    duplicates = int(out["event_id"].duplicated().sum())
    if duplicates:
        log.warning("dropping %d duplicate event ids", duplicates)
        out = out.drop_duplicates(subset="event_id", keep="first")

    log.info("prepared %d study-area events", len(out))
    return out.reset_index(drop=True)


def summarise(events: pd.DataFrame, by: str = "state_name_norm") -> pd.DataFrame:
    """Event counts by state or country — sanity check these after loading."""
    if by not in events.columns:
        raise KeyError(f"{by!r} is not a column on the events frame")
    return (
        events.groupby(by, dropna=False)
        .size()
        .sort_values(ascending=False)
        .rename("events")
        .reset_index()
    )
