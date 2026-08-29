"""The elevation confounder, pinned so it cannot come back quietly.

The hill mask admits the whole Ladakh and Tibetan plateau. Drawing controls
uniformly from it gave background cells a median elevation of 4,479 m against
1,433 m for cases, and `elev_mean` became the top SHAP feature at twice the
weight of anything else. That was the sampler, not the physics: the model had
learned to tell a high plateau from a mid-elevation slope, which is not the
question being asked.

These tests build a world where the plateau and the slope are interleaved in
space, so distance alone cannot separate them. Only elevation-band matching can
produce controls that resemble the cases.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.sampling import CaseControlSampler

SLOPE_M = 1400.0
PLATEAU_M = 4500.0


@pytest.fixture
def world():
    """Half slope, half plateau, alternating across a 3-degree grid.

    Interleaving matters. If the plateau sat in its own corner, the 25-300 km
    spatial annulus would exclude it by accident and the test would pass for
    the wrong reason.
    """
    lats = np.arange(28.0, 31.0, 0.15)
    lons = np.arange(77.0, 80.0, 0.15)
    rows = []
    cell_id = 0
    for lat in lats:
        for lon in lons:
            cell_id += 1
            rows.append({
                "cell_id": cell_id,
                "lat_c": round(float(lat), 3),
                "lon_c": round(float(lon), 3),
                "elev_mean": SLOPE_M if cell_id % 2 else PLATEAU_M,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def events(world):
    """Twenty events, every one of them on slope terrain."""
    slope = world[world["elev_mean"] == SLOPE_M].head(20).reset_index(drop=True)
    dates = pd.date_range("2010-06-01", periods=len(slope), freq="17D")
    return pd.DataFrame({
        "event_id": range(1, len(slope) + 1),
        "cell_id": slope["cell_id"],
        "latitude": slope["lat_c"],
        "longitude": slope["lon_c"],
        "date_id": [int(d.strftime("%Y%m%d")) for d in dates],
        "event_date": dates,
    })


@pytest.fixture
def calendar():
    days = pd.date_range("2010-01-01", "2012-12-31", freq="D")
    return pd.DataFrame({
        "date_id": days.strftime("%Y%m%d").astype(int),
        "day_of_year": days.dayofyear,
    })


@pytest.fixture
def sample(world, events, calendar):
    sampler = CaseControlSampler(events, world, calendar, seed=7)
    frame = sampler.build(negatives_per_positive=6)
    return frame.merge(world[["cell_id", "elev_mean"]], on="cell_id", how="left")


def test_controls_sit_at_the_elevation_of_the_cases(sample):
    """The gap that used to be 3,000 m must stay inside one band's tolerance."""
    cases = sample[sample["label"] == 1]["elev_mean"].median()
    controls = sample[sample["label"] == 0]["elev_mean"].median()

    assert abs(controls - cases) < 1000, (
        f"controls sit {controls - cases:+.0f} m from cases — the plateau is "
        "back in the control pool"
    )


def test_background_stratum_is_not_dominated_by_the_plateau(sample):
    """Background is the stratum the leak entered through."""
    background = sample[sample["stratum"] == "background"]
    assert not background.empty

    on_plateau = (background["elev_mean"] == PLATEAU_M).mean()
    assert on_plateau < 0.25, (
        f"{on_plateau:.0%} of background controls are plateau cells"
    )


def test_spatial_controls_respect_the_band_not_just_the_annulus(sample):
    """Distance alone would take slope and plateau in equal measure."""
    spatial = sample[sample["stratum"] == "spatial"]
    assert not spatial.empty

    on_plateau = (spatial["elev_mean"] == PLATEAU_M).mean()
    assert on_plateau < 0.25, (
        f"{on_plateau:.0%} of spatial controls are plateau cells — band "
        "matching is not being applied inside the annulus"
    )


def test_temporal_controls_stay_in_their_own_cell(sample):
    """A temporal control asks why THIS day failed when others did not."""
    temporal = sample[sample["stratum"] == "temporal"]
    cases = sample[sample["label"] == 1]

    assert set(temporal["cell_id"]).issubset(set(cases["cell_id"]))


def test_no_cell_date_pair_appears_twice(sample):
    assert not sample.duplicated(subset=["cell_id", "date_id"]).any()


def test_a_missing_elevation_column_does_not_crash_the_sampler(
    world, events, calendar
):
    """dim_cell without terrain should degrade, not explode."""
    bare = world.drop(columns=["elev_mean"])
    frame = CaseControlSampler(events, bare, calendar, seed=7).build(
        negatives_per_positive=4
    )
    assert not frame.empty
