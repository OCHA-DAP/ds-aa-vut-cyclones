import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def mon():
    os.environ.setdefault("DRY_RUN", "True")
    spec = importlib.util.spec_from_file_location(
        "monitor", ROOT / "pipelines" / "monitor_forecast_trigger.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_map_layers(mon):
    from src.monitoring.forecast import (
        interpolated,
        parse_forecast_csv,
        segment,
    )

    sim = ROOT / "src/monitoring/data/simulated_forecast.csv"
    track = parse_forecast_csv(sim.read_bytes()).track
    full = mon.swaths(interpolated(track))
    window = mon.swaths(segment(track, 24, 72))
    # "observed": the storm's path before this forecast, upstream (north)
    before = track.assign(lat=track["lat"] + 2.5)
    obs = mon.swaths(segment(before, 0, 30))
    layers = mon.map_layers(full, window, obs)

    def by_speed(gdf):
        gdf = gdf.to_crs(3832)
        return {int(s): g for s, g in zip(gdf["buffer_speed"], gdf.geometry)}

    seen, fcast = by_speed(layers["observed"]), by_speed(layers["forecast"])
    for speed in mon.SPEEDS:
        # as ds-storms-pipeline's forecast-only buffers: what was observed
        # at a speed is not drawn again as forecast at that speed
        assert fcast[speed].intersection(seen[speed]).area < 1e3  # m2
        # and the two together are exactly what the exposure counts
        both = fcast[speed].union(seen[speed])
        assert (
            both.symmetric_difference(mon.union(obs, full)[speed]).area < 1e3
        )
    # the trigger window: its 64 kt swath only, drawn as an outline
    assert list(layers["window"]["buffer_speed"]) == [64]


def test_union_keeps_missing(mon):
    from shapely.geometry import box

    a = {34: box(0, 0, 2, 2), 50: None, 64: None}
    b = {34: box(1, 0, 3, 2), 50: box(0, 0, 1, 1), 64: None}
    out = mon.union(a, b)
    assert out[34].equals(box(0, 0, 3, 2))
    assert out[50].equals(box(0, 0, 1, 1))
    assert out[64] is None


def test_cut_is_per_speed_and_keeps_missing(mon):
    from shapely.geometry import box

    a = {34: box(0, 0, 10, 10), 50: box(0, 0, 5, 5), 64: None}
    b = {34: box(5, 0, 10, 10), 50: None, 64: box(0, 0, 1, 1)}
    out = mon.cut(a, b)
    assert out[34].equals(box(0, 0, 5, 10))
    assert out[50].equals(box(0, 0, 5, 5))
    assert out[64] is None
