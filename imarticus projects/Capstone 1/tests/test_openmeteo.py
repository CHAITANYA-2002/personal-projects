"""Fetcher invariants that have already cost this project a backfill.

Two failures happened here for real and both were expensive, so both are pinned:

  a permanent refusal killed a run   one HTTP 400 on a window ERA5 has not
                                     reanalysed yet was retried four times and
                                     then raised, ending a fetch that still had
                                     ten thousand good windows to collect

  moving windows voided the cache    windows were merged per cell, so changing
                                     the sampled dates moved the boundaries and
                                     invalidated files holding exactly the days
                                     that were needed
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from src.data import openmeteo as om


GOOD_PAYLOAD = {
    "daily": {
        "time": ["2010-06-01", "2010-06-02"],
        "precipitation_sum": [12.0, 3.0],
    }
}


class _Response:
    """Just enough of requests.Response for the fetcher to read."""

    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


class _Session:
    """Answers 400 for any window in 2026, 200 for anything else."""

    def __init__(self, bad_year: str = "2026") -> None:
        self.calls = 0
        self.bad_year = bad_year

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if params["start_date"].startswith(self.bad_year):
            return _Response(400, {
                "error": True,
                "reason": "Parameter 'end_date' is out of allowed range",
            })
        return _Response(200, [GOOD_PAYLOAD] * len(params["latitude"].split(",")))


def _fetcher(tmp_path, session):
    return om.WeatherFetcher(
        cache_dir=tmp_path, session=session, sleep_seconds=0, batch_size=2
    )


def _plans():
    return [
        om.CellRequest(1, 30.0, 78.0, date(2026, 7, 15), date(2026, 8, 28)),
        om.CellRequest(2, 30.1, 78.1, date(2026, 7, 15), date(2026, 8, 28)),
        om.CellRequest(3, 30.2, 78.2, date(2010, 6, 1), date(2010, 6, 2)),
        om.CellRequest(4, 30.3, 78.3, date(2010, 6, 1), date(2010, 6, 2)),
    ]


def test_permanent_failure_does_not_end_the_run(tmp_path):
    """A 400 costs one batch, not the whole backfill."""
    session = _Session()
    fetcher = _fetcher(tmp_path, session)

    written = fetcher.fetch_many(_plans(), include_discharge=False)

    assert written == 2, "the good window should still have been written"


def test_permanent_failure_is_not_retried(tmp_path):
    """Backoff cannot make ERA5 produce a day it has not reanalysed."""
    session = _Session()
    fetcher = _fetcher(tmp_path, session)

    fetcher.fetch_many(_plans(), include_discharge=False)

    # Two batches, one request each. Four would mean the 400 went through the
    # retry loop, which is the bug this guards.
    assert session.calls == 2


def test_refused_windows_are_not_requeued(tmp_path):
    """A daemon cycling for hours must not re-ask the same refused question."""
    session = _Session()
    fetcher = _fetcher(tmp_path, session)
    plans = _plans()

    fetcher.fetch_many(plans, include_discharge=False)
    before = session.calls

    assert fetcher.pending(plans) == []
    fetcher.fetch_many(plans, include_discharge=False)
    assert session.calls == before, "a refused window was asked for twice"


def test_rate_limit_is_still_transient(tmp_path):
    """429 must keep its own path — it is temporary, unlike a 4xx refusal."""

    class RateLimited(_Session):
        def get(self, url, params=None, timeout=None):
            self.calls += 1
            return _Response(429, {"error": True, "reason": "quota"})

    session = RateLimited()
    fetcher = _fetcher(tmp_path, session)

    with pytest.raises(om.BudgetExhausted):
        fetcher._call(
            "http://example.invalid", _plans()[2:], date(2010, 6, 1),
            date(2010, 6, 2), daily="precipitation_sum",
        )


def test_cached_days_are_recognised_under_any_filename(tmp_path):
    """Coverage is what matters, not the name the window was fetched under.

    The old implementation compared filenames, so a shifted window boundary
    re-fetched days already sitting on disk. That cost most of one backfill.
    """
    session = _Session()
    fetcher = _fetcher(tmp_path, session)

    fetched = om.CellRequest(3, 30.2, 78.2, date(2010, 6, 1), date(2010, 6, 2))
    fetcher.fetch_many([fetched], include_discharge=False)

    # Same days, different boundaries — a different cache filename entirely.
    shifted = om.CellRequest(3, 30.2, 78.2, date(2010, 6, 2), date(2010, 6, 2))
    assert fetcher.cache_path(shifted) != fetcher.cache_path(fetched)
    assert fetcher.pending([shifted]) == []


def test_planned_windows_are_anchored_to_their_own_sample(tmp_path):
    """Rebuilding the sample must not move a window that did not change.

    Windows were once merged per cell. Adding one sampled date for a cell moved
    the boundaries of every window that cell already had, and the cache — keyed
    on (cell, start, end) — was voided wholesale.
    """
    import pandas as pd

    cells = pd.DataFrame({"cell_id": [7], "lat_c": [30.0], "lon_c": [78.0]})
    first = pd.DataFrame({"cell_id": [7], "date_id": [20100601]})
    second = pd.DataFrame({"cell_id": [7, 7], "date_id": [20100601, 20120901]})

    before = om.plan_requests(first, cells)
    after = om.plan_requests(second, cells)

    assert before[0] in after, "an unchanged sample moved its own window"


def test_error_reason_prefers_the_api_explanation():
    """The status code alone does not say which parameter was wrong."""
    body = _Response(400, {"error": True, "reason": "end_date out of range"})
    assert om._error_reason(body) == "end_date out of range"

    unparseable = _Response(400, None)
    assert om._error_reason(unparseable)
