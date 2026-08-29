"""Open-Meteo archive and flood fetcher.

This is the long pole of the whole pipeline, and the free tier is the reason.
Open-Meteo bills fractionally: a request is one call up to two weeks of data,
and roughly one more per additional fortnight, scaled by how many variables are
asked for. A ten-year pull for a single point therefore costs around 260 calls
against a 10,000 per day allowance — which is why the pipeline never fetches a
full gridded panel and instead pulls short windows around sampled cell-dates.

Three properties make the fetch survivable:

  resumable   one Parquet file per cell; an existing file is never refetched
  batched     several coordinates ride in one request (the API accepts
              comma-separated lat/lon lists and answers with a list)
  budgeted    spend is tracked per UTC day and the run stops before the wall
              rather than collecting a few thousand HTTP 429s

Rainfall and soil moisture come from the archive endpoint; river discharge is a
second call to the flood endpoint, which serves GloFAS. Both are unauthenticated.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
import requests

from config import settings

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 120
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0
_DAYS_PER_BILLED_UNIT = 14
_VARS_PER_BILLED_UNIT = 10

_ARCHIVE_COLUMNS = {
    "precipitation_sum": "precip_mm",
    "rain_sum": "rain_mm",
    "temperature_2m_max": "temp_max",
    "temperature_2m_min": "temp_min",
    "temperature_2m_mean": "temp_mean",
    "soil_moisture_0_to_7cm_mean": "sm_0_7",
    "soil_moisture_7_to_28cm_mean": "sm_7_28",
    "soil_moisture_28_to_100cm_mean": "sm_28_100",
    "et0_fao_evapotranspiration": "et0_mm",
    "wind_speed_10m_max": "wind_max",
}


@dataclass(frozen=True)
class CellRequest:
    """One cell and the window of days wanted for it."""

    cell_id: int
    latitude: float
    longitude: float
    start: date
    end: date

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1


def estimate_calls(n_days: int, n_vars: int) -> float:
    """Open-Meteo's fractional billing, as documented on their pricing page."""
    day_units = max(1.0, n_days / _DAYS_PER_BILLED_UNIT)
    var_units = max(1.0, n_vars / _VARS_PER_BILLED_UNIT)
    return day_units * var_units


class BudgetExhausted(RuntimeError):
    """Raised when the configured daily allowance is spent."""


class PermanentRequestError(requests.RequestException):
    """A request the API will refuse identically however often it is sent.

    Anything in the 4xx family other than 429 is a statement about the request,
    not about the moment: a window ERA5 has not reanalysed yet, a coordinate
    out of range, a malformed variable list. Retrying it four times with
    exponential backoff only delays the same answer, and letting it escape
    kills a backfill of tens of thousands of windows over one bad batch. It is
    raised immediately and skipped by the caller.
    """


class CallBudget:
    """Tracks API spend across runs so a resumed fetch does not overdraw.

    State is a small JSON file keyed by UTC date. Deliberately conservative:
    multi-location batching may well be cheaper than per-location billing, but
    assuming it is not means a run stops early rather than getting throttled.
    """

    def __init__(self, limit: int, state_path: Path | None = None) -> None:
        self.limit = limit
        self.state_path = state_path or (settings.WEATHER_RAW_DIR / "_budget.json")
        self._state = self._load()
        self._warned = False

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> dict[str, float]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("budget state unreadable (%s) — starting from zero", exc)
            return {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep only recent days so the file cannot grow without bound.
        recent = dict(sorted(self._state.items())[-14:])
        self.state_path.write_text(json.dumps(recent, indent=1), encoding="utf-8")

    @property
    def spent(self) -> float:
        return self._state.get(self._today(), 0.0)

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent)

    # The tracker counts by calendar day; Open-Meteo's allowance refills on a
    # rolling window. The two disagree by design, so this figure cannot be the
    # authority — blocking on it stops a fetch the API would happily serve.
    # HTTP 429 is the truth, and the fetcher already stops cleanly on it. This
    # stays as a warning, with a hard ceiling well above the real limit purely
    # as a runaway guard.
    RUNAWAY_MULTIPLE = 3.0

    def check(self, cost: float) -> None:
        if cost + self.spent > self.limit * self.RUNAWAY_MULTIPLE:
            raise BudgetExhausted(
                f"spent {self.spent:.0f} against a {self.limit} guideline — "
                f"past the {self.RUNAWAY_MULTIPLE:g}x runaway guard. Something "
                "is looping; stopping rather than hammering the API."
            )
        if cost > self.remaining and not self._warned:
            self._warned = True
            log.warning(
                "past the %d-call daily guideline (%.0f spent). Continuing, "
                "because the real limit is a rolling window and a 429 is what "
                "actually says stop.",
                self.limit, self.spent,
            )

    def charge(self, cost: float) -> None:
        self._state[self._today()] = self.spent + cost
        self._save()


class WeatherFetcher:
    """Pulls daily weather and river discharge, one Parquet file per cell."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        budget: CallBudget | None = None,
        batch_size: int | None = None,
        sleep_seconds: float | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = cache_dir or settings.WEATHER_RAW_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget or CallBudget(settings.OPENMETEO_DAILY_BUDGET)
        self.batch_size = batch_size or settings.OPENMETEO_BATCH_SIZE
        self.sleep_seconds = (
            settings.OPENMETEO_SLEEP_SECONDS if sleep_seconds is None else sleep_seconds
        )
        self.session = session or requests.Session()
        self._coverage_cache: dict[int, set[int]] | None = None
        # Windows the API has permanently refused this run. Kept so a daemon
        # cycling for hours does not re-ask the same impossible question every
        # twenty minutes.
        self._refused: set[tuple[int, date, date]] = set()

    # ------------------------------------------------------------------
    # cache
    # ------------------------------------------------------------------

    def cache_path(self, request: CellRequest) -> Path:
        """One file per cell and window.

        Keying on the cell alone would collide: the same cell is sampled on
        several dates, each needing its own window, and the second fetch would
        silently overwrite the first.
        """
        return self.cache_dir / (
            f"cell_{request.cell_id}"
            f"_{request.start:%Y%m%d}_{request.end:%Y%m%d}.parquet"
        )

    def is_cached(self, request: CellRequest) -> bool:
        return self.cache_path(request).exists()

    def pending(self, requests_: Iterable[CellRequest]) -> list[CellRequest]:
        """Drop windows whose days are already on disk, under any filename.

        Checking for the exact cache file is the obvious implementation and it
        throws away work. Cache files are named for the window they were fetched
        as, so when the sampler changes and window boundaries move, a file
        holding precisely the needed days no longer matches by name and gets
        re-fetched. That cost most of one backfill.

        The days themselves do not care what file they arrived in, so coverage
        is what is checked: a window is pending only if some day it needs is not
        already cached for that cell.
        """
        requests_ = list(requests_)
        if not requests_:
            return []

        coverage = self._coverage_index()
        outstanding: list[CellRequest] = []
        recovered = 0

        for req in requests_:
            if (req.cell_id, req.start, req.end) in self._refused:
                continue
            have = coverage.get(req.cell_id)
            if have and _window_days(req).issubset(have):
                recovered += 1
                continue
            outstanding.append(req)

        if self._refused:
            log.info("%d windows permanently refused this run — not retried",
                     len(self._refused))
        if recovered:
            log.info(
                "%d windows already covered by cached days under other "
                "filenames — not refetching", recovered,
            )
        return outstanding

    def _coverage_index(self) -> dict[int, set[int]]:
        """cell_id -> set of date_ids present anywhere in the cache."""
        if getattr(self, "_coverage_cache", None) is not None:
            return self._coverage_cache

        index: dict[int, set[int]] = {}
        for path in sorted(self.cache_dir.glob("cell_*.parquet")):
            try:
                frame = pd.read_parquet(path, columns=["cell_id", "date_id"])
            except Exception as exc:
                log.warning("unreadable cache file %s: %s", path.name, exc)
                continue
            for cell_id, group in frame.groupby("cell_id"):
                index.setdefault(int(cell_id), set()).update(
                    group["date_id"].astype(int).tolist()
                )

        total_days = sum(len(days) for days in index.values())
        log.info("cache holds %d cell-days across %d cells", total_days, len(index))
        self._coverage_cache = index
        return index

    def load_all_cached(self) -> pd.DataFrame:
        """Every cached window, de-duplicated on (cell_id, date_id).

        Windows for the same cell overlap by design, so the same day can appear
        in several files. Keeping the first is safe — they are identical.
        """
        files = sorted(self.cache_dir.glob("cell_*.parquet"))
        if not files:
            return pd.DataFrame()

        frames = [pd.read_parquet(path) for path in files]
        combined = pd.concat(frames, ignore_index=True)
        before = len(combined)
        combined = combined.drop_duplicates(subset=["cell_id", "date_id"], keep="first")
        log.info(
            "loaded %d cached windows: %d rows, %d after de-duplication",
            len(files), before, len(combined),
        )
        return combined.sort_values(["cell_id", "date_id"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # fetching
    # ------------------------------------------------------------------

    def fetch_many(
        self,
        requests_: Sequence[CellRequest],
        include_discharge: bool = True,
    ) -> int:
        """Fetch every uncached cell. Returns the number newly written.

        Requests are grouped by identical date window, because a single HTTP
        call can carry many coordinates but only one window.
        """
        todo = self.pending(requests_)
        skipped = len(requests_) - len(todo)
        if skipped:
            log.info("%d cells already cached — skipping", skipped)
        if not todo:
            log.info("nothing to fetch")
            return 0

        by_window: dict[tuple[date, date], list[CellRequest]] = {}
        for req in todo:
            by_window.setdefault((req.start, req.end), []).append(req)

        log.info(
            "fetching %d cells across %d date windows | budget remaining %.0f",
            len(todo), len(by_window), self.budget.remaining,
        )

        written = 0
        refused = 0
        for (start, end), group in by_window.items():
            for batch in _chunks(group, self.batch_size):
                try:
                    written += self._fetch_batch(batch, start, end, include_discharge)
                except BudgetExhausted:
                    log.warning(
                        "stopping early: %d cells written, %d still pending",
                        written, len(todo) - written,
                    )
                    return written
                except PermanentRequestError as exc:
                    # One unanswerable window is not a reason to abandon the
                    # other 10,000. Record it, say so, keep going.
                    refused += len(batch)
                    for req in batch:
                        self._refused.add((req.cell_id, req.start, req.end))
                    log.warning(
                        "skipping %d cells for %s..%s — %s",
                        len(batch), start, end, exc,
                    )
        if refused:
            log.warning(
                "%d windows were permanently refused and are NOT in the cache",
                refused,
            )
        log.info("fetch complete: %d cells written", written)
        return written

    def _fetch_batch(
        self,
        batch: Sequence[CellRequest],
        start: date,
        end: date,
        include_discharge: bool,
    ) -> int:
        n_days = (end - start).days + 1
        # Billing is per coordinate, not per request. This was measured, not
        # assumed: batching eight locations per request, the quota ran out after
        # 1,385 windows, which is 1,385 x 3.2 x 2 ~= 8,900 units against a
        # 10,000/day allowance. Batching saves round trips and nothing else.
        archive_cost = (
            estimate_calls(n_days, len(settings.OPENMETEO_DAILY_VARS)) * len(batch)
        )
        flood_cost = (
            estimate_calls(n_days, 1) * len(batch) if include_discharge else 0.0
        )

        self.budget.check(archive_cost + flood_cost)

        archive = self._call(
            settings.OPENMETEO_ARCHIVE_URL,
            batch, start, end,
            daily=",".join(settings.OPENMETEO_DAILY_VARS),
        )
        self.budget.charge(archive_cost)

        discharge = None
        if include_discharge:
            try:
                discharge = self._call(
                    settings.OPENMETEO_FLOOD_URL,
                    batch, start, end,
                    daily="river_discharge",
                )
                self.budget.charge(flood_cost)
            except requests.RequestException as exc:
                # Not every coordinate sits on a modelled river reach. Losing
                # discharge is survivable; losing rainfall is not.
                log.warning("discharge unavailable for this batch: %s", exc)

        written = 0
        for index, req in enumerate(batch):
            frame = _archive_to_frame(archive[index], req)
            if frame.empty:
                log.warning("cell %d returned no rows", req.cell_id)
                continue
            if discharge is not None:
                frame = _attach_discharge(frame, discharge[index])
            frame.to_parquet(self.cache_path(req), index=False)
            self._coverage_cache = None      # a new file changes what is covered
            written += 1

        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return written

    # ------------------------------------------------------------------
    # forecast
    # ------------------------------------------------------------------

    def fetch_forecast(
        self,
        cells: pd.DataFrame,
        past_days: int = 60,
        forecast_days: int = 16,
        include_discharge: bool = True,
    ) -> pd.DataFrame:
        """Recent history and the forecast in one pass, for daily scoring.

        The forecast endpoint serves both sides of today, which matters: the
        features the model needs are antecedent — 30 and 60-day accumulations —
        so a forecast alone is useless without the run-up that precedes it.
        """
        frames: list[pd.DataFrame] = []
        rows = list(cells.itertuples(index=False))

        for batch in _chunks(rows, self.batch_size):
            params_batch = [
                CellRequest(int(row.cell_id), float(row.lat_c), float(row.lon_c),
                            date.today(), date.today())
                for row in batch
            ]
            try:
                payloads = self._call_forecast(
                    settings.OPENMETEO_FORECAST_URL, params_batch,
                    past_days, forecast_days,
                    daily=",".join(settings.OPENMETEO_DAILY_VARS),
                )
            except BudgetExhausted:
                # Partial coverage is a usable forecast; a crash is not. Score
                # what was retrieved and say plainly how much of the map it is.
                log.warning(
                    "daily quota reached after %d cells — scoring the %d "
                    "already fetched and leaving the rest for the next run",
                    len(frames), len(frames),
                )
                break
            discharge = None
            if include_discharge:
                try:
                    discharge = self._call_forecast(
                        settings.OPENMETEO_FLOOD_URL, params_batch,
                        past_days, forecast_days, daily="river_discharge",
                    )
                except requests.RequestException as exc:
                    log.warning("forecast discharge unavailable: %s", exc)

            for index, req in enumerate(params_batch):
                frame = _archive_to_frame(payloads[index], req)
                if frame.empty:
                    continue
                if discharge is not None:
                    frame = _attach_discharge(frame, discharge[index])
                frames.append(frame)

            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)

        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        log.info("forecast: %d rows across %d cells",
                 len(combined), combined["cell_id"].nunique())
        return combined.sort_values(["cell_id", "date_id"]).reset_index(drop=True)

    def _call_forecast(
        self,
        url: str,
        batch: Sequence[CellRequest],
        past_days: int,
        forecast_days: int,
        daily: str,
    ) -> list[dict]:
        params = {
            "latitude": ",".join(f"{req.latitude:.4f}" for req in batch),
            "longitude": ",".join(f"{req.longitude:.4f}" for req in batch),
            "past_days": past_days,
            "forecast_days": forecast_days,
            "daily": daily,
            "timezone": "GMT",
        }
        response = self.session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        if response.status_code == 429:
            raise BudgetExhausted(
                "Open-Meteo daily quota is spent — the forecast endpoint shares "
                "the same allowance as the archive. Rerun after it resets."
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]

    def _call(
        self,
        url: str,
        batch: Sequence[CellRequest],
        start: date,
        end: date,
        daily: str,
    ) -> list[dict]:
        params = {
            "latitude": ",".join(f"{req.latitude:.4f}" for req in batch),
            "longitude": ",".join(f"{req.longitude:.4f}" for req in batch),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": daily,
            "timezone": "GMT",
        }

        last_error: Exception | None = None
        rate_limited = 0

        for attempt in range(_MAX_RETRIES):
            try:
                response = self.session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
                if response.status_code == 429:
                    rate_limited += 1
                    # Backing off cannot recover a spent daily quota, only a
                    # burst limit. Two refusals in a row means the day is done,
                    # and the run should end cleanly with its cache intact
                    # rather than grinding through retries to the same wall.
                    if rate_limited >= 2:
                        raise BudgetExhausted(
                            "Open-Meteo returned 429 twice — the daily quota is "
                            "spent. Cached windows are kept; rerun tomorrow to "
                            "continue where this stopped."
                        )
                    raise requests.HTTPError("rate limited", response=response)
                if 400 <= response.status_code < 500:
                    raise PermanentRequestError(
                        f"HTTP {response.status_code} for {start}..{end}: "
                        f"{_error_reason(response)}",
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                # One coordinate returns an object, several return a list.
                return payload if isinstance(payload, list) else [payload]
            except (BudgetExhausted, PermanentRequestError):
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                wait = _BACKOFF_BASE ** attempt
                log.warning(
                    "request failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt + 1, _MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)

        raise requests.RequestException(
            f"giving up on {url} after {_MAX_RETRIES} attempts"
        ) from last_error


# --------------------------------------------------------------------------
# response shaping
# --------------------------------------------------------------------------

def _error_reason(response: requests.Response) -> str:
    """Open-Meteo explains its 4xx in the body; the status alone does not."""
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()[:200] or "no reason given"
    if isinstance(payload, dict) and payload.get("reason"):
        return str(payload["reason"])
    return str(payload)[:200]


def _archive_to_frame(payload: dict, req: CellRequest) -> pd.DataFrame:
    daily = payload.get("daily")
    if not daily or not daily.get("time"):
        return pd.DataFrame()

    frame = pd.DataFrame({"date": pd.to_datetime(daily["time"])})
    for source, target in _ARCHIVE_COLUMNS.items():
        frame[target] = daily.get(source, [None] * len(frame))

    frame.insert(0, "cell_id", req.cell_id)
    frame.insert(1, "date_id", frame["date"].dt.strftime("%Y%m%d").astype("int64"))
    frame["date"] = frame["date"].dt.date
    return frame


def _attach_discharge(frame: pd.DataFrame, payload: dict) -> pd.DataFrame:
    daily = payload.get("daily") or {}
    times = daily.get("time")
    values = daily.get("river_discharge")
    if not times or not values:
        frame["river_discharge"] = None
        return frame

    lookup = pd.DataFrame(
        {
            "date_id": pd.to_datetime(times).strftime("%Y%m%d").astype("int64"),
            "river_discharge": values,
        }
    )
    return frame.merge(lookup, on="date_id", how="left")


def _window_days(request: CellRequest) -> set[int]:
    """Every date_id the window spans, as YYYYMMDD integers."""
    days = pd.date_range(request.start, request.end, freq="D")
    return set(days.strftime("%Y%m%d").astype(int).tolist())


def _chunks(items: Sequence[CellRequest], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def plan_requests(
    samples: pd.DataFrame,
    cells: pd.DataFrame,
    window_days: int | None = None,
) -> list[CellRequest]:
    """One fixed window per sampled cell-date.

    An earlier version merged overlapping windows within a cell, which was 21%
    cheaper — and turned out to be a false economy. The cache is keyed on
    (cell, start, end), and merged boundaries move whenever the sampled dates
    for that cell change. Fixing a bias in the sampler therefore invalidated
    almost the entire backfill: 2,190 fetched windows, of which 232 survived.

    A window anchored to its own sample date never moves. Rebuilding the sample
    now only re-fetches the samples that actually changed, and the 21% is a
    small price for that.
    """
    window_days = window_days or settings.FEATURE_WINDOW_DAYS
    span = timedelta(days=window_days - 1)

    centroids = cells.set_index("cell_id")[["lat_c", "lon_c"]]
    plans: list[CellRequest] = []
    missing_cells = 0

    for row in samples.itertuples(index=False):
        cell_id = int(row.cell_id)
        if cell_id not in centroids.index:
            missing_cells += 1
            continue

        end = datetime.strptime(str(row.date_id), "%Y%m%d").date()
        plans.append(
            CellRequest(
                cell_id,
                float(centroids.at[cell_id, "lat_c"]),
                float(centroids.at[cell_id, "lon_c"]),
                end - span,
                end,
            )
        )

    if missing_cells:
        log.warning("%d samples have no centroid in dim_cell", missing_cells)

    # Identical (cell, window) pairs can legitimately repeat; fetch each once.
    unique = list(dict.fromkeys(plans))
    log.info(
        "planned %d fetch windows for %d cells (%d sampled cell-dates)",
        len(unique), samples["cell_id"].nunique(), len(samples),
    )
    return unique


def summarise_plan(
    requests_: Sequence[CellRequest],
    batch_size: int,
    include_discharge: bool = True,
) -> dict[str, float]:
    """Cost of a fetch before committing to it. Always look at this first.

    Counted per coordinate. That is the measured behaviour, not a reading of the
    documentation: batching eight locations per request, the daily quota ran out
    after 1,385 windows, which matches per-coordinate billing and rules out
    per-request billing by a factor of eight.
    """
    n_vars = len(settings.OPENMETEO_DAILY_VARS)
    if not requests_:
        return {"windows": 0, "http_requests": 0, "archive_calls": 0.0,
                "flood_calls": 0.0, "total_calls": 0.0, "days_at_budget": 0.0}

    archive = sum(estimate_calls(req.n_days, n_vars) for req in requests_)
    flood = (
        sum(estimate_calls(req.n_days, 1) for req in requests_)
        if include_discharge else 0.0
    )
    total = archive + flood
    batches = math.ceil(len(requests_) / max(1, batch_size))

    return {
        "windows": len(requests_),
        "http_requests": batches * 2,
        "archive_calls": round(archive, 1),
        "flood_calls": round(flood, 1),
        "total_calls": round(total, 1),
        "days_at_budget": round(
            total / max(1, settings.OPENMETEO_DAILY_BUDGET), 2
        ),
    }
