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


def test_map_layers_never_overlap(mon):
    from src.monitoring.forecast import parse_forecast_csv, segment

    sim = ROOT / "src/monitoring/data/simulated_forecast.csv"
    track = parse_forecast_csv(sim.read_bytes()).track
    window = mon.swaths(segment(track, 24, 72))
    # "observed": the storm's path before this forecast, upstream (north)
    before = track.assign(lat=track["lat"] + 2.5)
    obs = mon.swaths(segment(before, 0, 30))
    layers = mon.map_layers(track, window, obs)
    win = layers["window"].to_crs(3832).union_all()
    other = layers["other"].to_crs(3832).union_all()
    seen = layers["observed"].to_crs(3832).union_all()
    full34 = mon.swaths(segment(track, 0, 120))[34]
    assert win.intersection(other).area < 1e3  # m2: touching edges only
    assert seen.intersection(full34).area < 1e3
    assert not other.is_empty and not seen.is_empty


def test_cut_is_per_speed_and_keeps_missing(mon):
    from shapely.geometry import box

    a = {34: box(0, 0, 10, 10), 50: box(0, 0, 5, 5), 64: None}
    b = {34: box(5, 0, 10, 10), 50: None, 64: box(0, 0, 1, 1)}
    out = mon.cut(a, b)
    assert out[34].equals(box(0, 0, 5, 10))
    assert out[50].equals(box(0, 0, 5, 5))
    assert out[64] is None
