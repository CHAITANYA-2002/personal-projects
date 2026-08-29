"""Case-control sampling — the decision the whole model rests on.

There are 1,719 recorded landslides. Everything else across the study grid and
ten years is nominally a negative, which is tens of millions of cell-days. Draw
those at random and two things go wrong, both of them quietly:

  * the model reaches 99.99% accuracy by never predicting a landslide
  * a random negative is usually a dry January day on flat ground, so the model
    learns to separate monsoon from winter and mountain from plain — both
    trivially true, neither useful, and together they produce a system that
    fires on every hill in India every July

So the negatives are built in three strata, each removing a specific confound:

  temporal    same cell, different date, season-matched
              forces the model to explain why THIS monsoon day failed when
              hundreds of other monsoon days in the same cell did not

  spatial     nearby hill cell, same date
              removes "it was raining across the whole state" — the comparison
              cell saw the same weather system and held

  background  random hill cell, random date
              keeps the model anchored to the true base rate

One guard rail matters as much as the strata. The catalogue records *reported*
landslides, and in remote terrain many go unreported. A candidate negative close
in space and time to a known event is therefore discarded rather than labelled
zero — teaching the model that a probable landslide is a confident negative is
worse than not using the sample at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import settings

log = logging.getLogger(__name__)

_EARTH_RADIUS_KM = 6371.0

# Spatial controls are drawn from this annulus around the case cell. The inner
# edge clears the exclusion buffer; the outer keeps the control inside the same
# weather system rather than a different climate zone.
_SPATIAL_MIN_KM = 25.0
_SPATIAL_MAX_KM = 300.0

# Temporal controls must land within this many days of the event's day-of-year,
# compared circularly so late December and early January count as adjacent.
_SEASON_WINDOW_DAYS = 30

# Spatial and background controls are matched on elevation as well as season.
#
# Without this the design leaks badly, and quietly. The hill mask admits any
# cell steeper than 5 degrees, which includes the whole Ladakh and Tibetan
# plateau — high, comparatively flat, and outside the monsoon. Sampling controls
# from that pool gave a median elevation of 4,479 m against 1,433 m for cases,
# and the model could then separate the classes on altitude alone. Elevation
# duly appeared as the top feature by SHAP, at twice the weight of anything
# else, which read as physics and was an artefact of the sampler.
#
# This is the same "mountain versus plain" failure the three strata exist to
# prevent, wearing different clothes: high plateau versus mid-elevation slope.
_ELEVATION_BAND_M = 500
_ELEVATION_BAND_TOLERANCE = 1   # bands either side that still count as a match


@dataclass(frozen=True)
class SampleCounts:
    cases: int
    temporal: int
    spatial: int
    background: int

    @property
    def total(self) -> int:
        return self.cases + self.temporal + self.spatial + self.background

    @property
    def negatives(self) -> int:
        return self.temporal + self.spatial + self.background


def haversine_km(
    lat1: np.ndarray, lon1: np.ndarray,
    lat2: float, lon2: float,
) -> np.ndarray:
    """Great-circle distance from many points to one, in kilometres."""
    lat1_r, lon1_r = np.radians(lat1), np.radians(lon1)
    lat2_r, lon2_r = math_radians(lat2), math_radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def math_radians(value: float) -> float:
    return float(np.radians(value))


def circular_day_distance(doy_a: np.ndarray, doy_b: int) -> np.ndarray:
    """Distance between days of year, wrapping across the new year."""
    raw = np.abs(doy_a - doy_b)
    return np.minimum(raw, 365 - raw)


def build_exclusion_set(
    events: pd.DataFrame,
    cells: pd.DataFrame,
    radius_km: float | None = None,
    days: int | None = None,
) -> set[tuple[int, int]]:
    """(cell_id, date_id) pairs too close to a known event to use as negatives.

    Returned as a set because membership is tested millions of times during
    sampling and anything slower dominates the runtime.
    """
    radius_km = settings.EXCLUSION_RADIUS_KM if radius_km is None else radius_km
    days = settings.EXCLUSION_DAYS if days is None else days

    cell_ids = cells["cell_id"].to_numpy()
    lats = cells["lat_c"].to_numpy(dtype=float)
    lons = cells["lon_c"].to_numpy(dtype=float)

    excluded: set[tuple[int, int]] = set()
    dates = pd.to_datetime(events["event_date"])

    for (lat, lon), event_date in zip(
        zip(events["latitude"], events["longitude"]), dates
    ):
        near = cell_ids[haversine_km(lats, lons, float(lat), float(lon)) <= radius_km]
        window = pd.date_range(
            event_date - pd.Timedelta(days=days),
            event_date + pd.Timedelta(days=days),
            freq="D",
        )
        date_ids = [int(day.strftime("%Y%m%d")) for day in window]
        for cell_id in near:
            for date_id in date_ids:
                excluded.add((int(cell_id), date_id))

    log.info(
        "exclusion buffer: %d cell-date pairs within %.0f km and +/-%d days",
        len(excluded), radius_km, days,
    )
    return excluded


class CaseControlSampler:
    """Draws the modelling frame from events, the hill mask and the calendar."""

    def __init__(
        self,
        events: pd.DataFrame,
        hill_cells: pd.DataFrame,
        dates: pd.DataFrame,
        seed: int = 42,
    ) -> None:
        if hill_cells.empty:
            raise ValueError(
                "no hill cells supplied — run scripts/03_build_terrain.py so "
                "dim_cell.is_hill is populated before sampling"
            )

        self.events = events.reset_index(drop=True)
        self.cells = hill_cells.reset_index(drop=True)
        self.dates = dates.reset_index(drop=True)
        self.rng = np.random.default_rng(seed)

        self._cell_ids = self.cells["cell_id"].to_numpy()
        self._lats = self.cells["lat_c"].to_numpy(dtype=float)
        self._lons = self.cells["lon_c"].to_numpy(dtype=float)
        self._bands = self._elevation_bands(self.cells)
        self._case_bands = self._case_elevation_bands()

        self._date_ids = self.dates["date_id"].to_numpy()
        self._doys = self.dates["day_of_year"].to_numpy()

        self.excluded = build_exclusion_set(self.events, self.cells)

    # ------------------------------------------------------------------

    @staticmethod
    def _elevation_bands(cells: pd.DataFrame) -> np.ndarray:
        if "elev_mean" not in cells.columns:
            return np.zeros(len(cells), dtype=float)
        elevation = cells["elev_mean"].astype(float).fillna(-1)
        return np.floor(elevation / _ELEVATION_BAND_M).to_numpy()

    def _case_elevation_bands(self) -> dict[int, float]:
        """Elevation band of the cell each event sits in."""
        lookup = dict(zip(self._cell_ids, self._bands))
        return {
            int(row.cell_id): lookup.get(int(row.cell_id), np.nan)
            for row in self.events.itertuples(index=False)
        }

    def _band_matches(self, band: float) -> np.ndarray:
        """Boolean mask of cells within tolerance of a target elevation band."""
        if band != band:                                  # NaN target
            return np.ones(len(self._bands), dtype=bool)
        return np.abs(self._bands - band) <= _ELEVATION_BAND_TOLERANCE

    def build(self, negatives_per_positive: int | None = None) -> pd.DataFrame:
        ratio = negatives_per_positive or settings.NEGATIVES_PER_POSITIVE
        weights = settings.STRATUM_WEIGHTS

        per_case = {
            name: ratio * share for name, share in weights.items()
        }
        log.info(
            "target: %d cases, %.1f negatives each (%s)",
            len(self.events), ratio,
            ", ".join(f"{k} {v:.1f}" for k, v in per_case.items()),
        )

        rows: list[dict] = []
        rows.extend(self._cases())

        taken: set[tuple[int, int]] = {
            (row["cell_id"], row["date_id"]) for row in rows
        }

        rows.extend(self._temporal_controls(per_case["temporal"], taken))
        rows.extend(self._spatial_controls(per_case["spatial"], taken))
        rows.extend(self._background_controls(per_case["background"], taken))

        frame = pd.DataFrame(rows)
        frame = frame.drop_duplicates(subset=["cell_id", "date_id"], keep="first")
        frame = self._assign_splits(frame)

        self._log_composition(frame)
        return frame.reset_index(drop=True)

    # ------------------------------------------------------------------

    def _cases(self) -> list[dict]:
        return [
            {
                "cell_id": int(row.cell_id),
                "date_id": int(row.date_id),
                "label": 1,
                "stratum": "case",
                "event_id": int(row.event_id),
            }
            for row in self.events.itertuples(index=False)
        ]

    def _temporal_controls(self, per_case: float, taken: set) -> list[dict]:
        rows: list[dict] = []
        for row in self.events.itertuples(index=False):
            wanted = self._draw_count(per_case)
            doy = int(pd.Timestamp(row.event_date).dayofyear)
            candidates = self._date_ids[
                circular_day_distance(self._doys, doy) <= _SEASON_WINDOW_DAYS
            ]
            rows.extend(
                self._sample_pairs(
                    cell_ids=np.full(len(candidates), int(row.cell_id)),
                    date_ids=candidates,
                    wanted=wanted,
                    stratum="temporal",
                    taken=taken,
                )
            )
        return rows

    def _spatial_controls(self, per_case: float, taken: set) -> list[dict]:
        rows: list[dict] = []
        for row in self.events.itertuples(index=False):
            wanted = self._draw_count(per_case)
            distance = haversine_km(
                self._lats, self._lons, float(row.latitude), float(row.longitude)
            )
            band = self._case_bands.get(int(row.cell_id), float("nan"))
            in_annulus = (distance >= _SPATIAL_MIN_KM) & (distance <= _SPATIAL_MAX_KM)
            eligible = in_annulus & self._band_matches(band)

            # Fall back to the plain annulus rather than dropping the case: a
            # control at the wrong elevation is worse than ideal, no control at
            # all is worse still.
            if not eligible.any():
                eligible = in_annulus
            near = self._cell_ids[eligible]
            if near.size == 0:
                continue
            rows.extend(
                self._sample_pairs(
                    cell_ids=near,
                    date_ids=np.full(len(near), int(row.date_id)),
                    wanted=wanted,
                    stratum="spatial",
                    taken=taken,
                )
            )
        return rows

    def _background_controls(self, per_case: float, taken: set) -> list[dict]:
        """Random hill cells, but drawn from the case elevation distribution.

        A uniform draw over the hill mask is dominated by the high plateau,
        which is nothing like the terrain events occur on. Picking a case first
        and then a cell in its elevation band keeps the background stratum
        comparable while still being a random background.
        """
        wanted = int(round(per_case * len(self.events)))
        rows: list[dict] = []
        attempts = 0
        max_attempts = wanted * 40

        case_cells = list(self._case_bands)

        while len(rows) < wanted and attempts < max_attempts:
            attempts += 1
            anchor = int(self.rng.choice(case_cells))
            pool = self._cell_ids[self._band_matches(self._case_bands[anchor])]
            if pool.size == 0:
                pool = self._cell_ids
            cell_id = int(self.rng.choice(pool))
            date_id = int(self.rng.choice(self._date_ids))
            key = (cell_id, date_id)
            if key in taken or key in self.excluded:
                continue
            taken.add(key)
            rows.append({
                "cell_id": cell_id, "date_id": date_id,
                "label": 0, "stratum": "background", "event_id": None,
            })

        if len(rows) < wanted:
            log.warning(
                "background stratum short: %d of %d after %d attempts",
                len(rows), wanted, attempts,
            )
        return rows

    # ------------------------------------------------------------------

    def _sample_pairs(
        self,
        cell_ids: np.ndarray,
        date_ids: np.ndarray,
        wanted: int,
        stratum: str,
        taken: set,
    ) -> list[dict]:
        if wanted <= 0 or cell_ids.size == 0:
            return []

        order = self.rng.permutation(len(cell_ids))
        picked: list[dict] = []

        for index in order:
            if len(picked) >= wanted:
                break
            key = (int(cell_ids[index]), int(date_ids[index]))
            if key in taken or key in self.excluded:
                continue
            taken.add(key)
            picked.append({
                "cell_id": key[0], "date_id": key[1],
                "label": 0, "stratum": stratum, "event_id": None,
            })

        return picked

    def _draw_count(self, expected: float) -> int:
        """Turn a fractional target into an integer without biasing the total."""
        base = int(expected)
        remainder = expected - base
        return base + int(self.rng.random() < remainder)

    def _assign_splits(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Blocked in time, never random — a random split leaks the future."""
        train_end = int(settings.SPLIT_TRAIN_END.strftime("%Y%m%d"))
        val_end = int(settings.SPLIT_VAL_END.strftime("%Y%m%d"))

        frame = frame.copy()
        frame["split"] = np.where(
            frame["date_id"] <= train_end, "train",
            np.where(frame["date_id"] <= val_end, "val", "test"),
        )
        return frame

    def _log_composition(self, frame: pd.DataFrame) -> None:
        counts = frame["stratum"].value_counts()
        log.info("--- sample composition ---")
        for stratum, count in counts.items():
            log.info("%-12s %7d", stratum, count)

        positives = int((frame["label"] == 1).sum())
        negatives = int((frame["label"] == 0).sum())
        log.info("%-12s %7d", "positives", positives)
        log.info("%-12s %7d", "negatives", negatives)
        log.info("%-12s %7.1f", "ratio 1:", negatives / max(1, positives))

        log.info("--- split ---")
        for split, group in frame.groupby("split"):
            pos = int((group["label"] == 1).sum())
            log.info("%-8s %7d rows, %5d positives (%.2f%%)",
                     split, len(group), pos, 100 * pos / len(group))
