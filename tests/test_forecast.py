from pathlib import Path

import pandas as pd
import pytest

from src.monitoring import forecast as fcm
from src.monitoring.forecast import ForecastParseError, parse_forecast_csv

SIM = Path(__file__).parents[1] / "src/monitoring/data/simulated_forecast.csv"
COLS = (
    "Latitude,Longitude,Category,MeanWind,Uncertainty,HurricaneRadius,"
    "NEHurricaneRadius,SEHurricaneRadius,SWHurricaneRadius,NWHurricaneRadius"
)


def make_csv(rows, track_type="Official Forecast Track"):
    head = [
        f"# trackType={track_type},baseTime=2026-02-01T00:00:00Z",
        "# DisturbanceId=2526-07F",
        "# CycloneName=TEST NAME",
        "# MathType of Data",
        "(Time->(...))",
        "# Column Headings",
        "Time[fmt=yyyy-MM-dd'T'HH:mm:ss'Z']," + COLS,
        "# Start of data",
    ]
    return ("\n".join(head + rows) + "\n").encode()


ROWS = [
    "2026-02-01T00:00:00Z,-15.0,167.0,3.0,70.0,20.0,30.0,NaN,NaN,NaN,NaN",
    "2026-02-01T06:00:00Z,-15.5,167.2,NaN,NaN,30.0,NaN,40.0,20.0,10.0,5.0",
    "2026-02-01T12:00:00Z,-16.0,167.4,NaN,NaN,40.0,NaN,NaN,NaN,NaN,NaN",
]


def test_header_and_track():
    fc = parse_forecast_csv(make_csv(ROWS))
    assert fc.track_type == fcm.OFFICIAL_TRACK
    assert fc.base_time == pd.Timestamp("2026-02-01T00:00:00")
    assert fc.disturbance_id == "2526-07F"
    assert fc.cyclone_name == "Test name"
    assert fc.season == 2026
    assert list(fc.track["leadtime"]) == [0, 6, 12]
    assert list(fc.track["lon"]) == [167.0, 167.2, 167.4]


def test_symmetric_radius_used_only_without_quadrants():
    t = parse_forecast_csv(make_csv(ROWS)).track
    # row 0: only the symmetric radius -> all four quadrants
    assert (
        t.loc[0, ["r64_ne", "r64_se", "r64_sw", "r64_nw"]].tolist()
        == [30.0] * 4
    )
    # row 1: quadrant radii as given
    assert t.loc[1, ["r64_ne", "r64_se", "r64_sw", "r64_nw"]].tolist() == [
        40.0,
        20.0,
        10.0,
        5.0,
    ]
    # row 2: no 64 kt winds -> zero, never NaN
    assert t.loc[2, ["r64_ne", "r64_nw"]].tolist() == [0.0, 0.0]
    assert not t.filter(like="r34_").isna().any().any()


def test_no_trailing_radius_after_interpolation():
    seg = fcm.segment(parse_forecast_csv(make_csv(ROWS)).track, 6, 12)
    assert seg["leadtime"].iloc[0] == 6 and seg["leadtime"].iloc[-1] == 12
    assert seg["r64_ne"].iloc[-1] == 0  # tapers to zero, not carried on


@pytest.mark.parametrize(
    "row",
    [
        "2026-02-01T18:00:00Z,-16.5,167.6,NaN,NaN,40.0,abc,NaN,NaN,NaN,NaN",
        "2026-02-01T18:00:00Z,-16.5,inf,NaN,NaN,40.0,NaN,NaN,NaN,NaN,NaN",
        "2026-02-01T18:00:00Z,NaN,167.6,NaN,NaN,40.0,NaN,NaN,NaN,NaN,NaN",
        "not-a-time,-16.5,167.6,NaN,NaN,40.0,NaN,NaN,NaN,NaN,NaN",
    ],
)
def test_unreadable_values_fail(row):
    with pytest.raises(ForecastParseError):
        parse_forecast_csv(make_csv(ROWS + [row]))


def test_track_type_is_read_not_assumed():
    fc = parse_forecast_csv(make_csv(ROWS, track_type="GP Forecast Track"))
    assert fc.track_type == "GP Forecast Track"


def test_simulated_forecast_reaches_both_windows():
    fc = parse_forecast_csv(SIM.read_bytes())
    assert fc.track_type == fcm.OFFICIAL_TRACK
    assert fc.track["leadtime"].max() == 120
    assert (fc.track["r64_ne"] == 30).all()
    for lo, hi in ((24, 72), (72, 120)):
        seg = fcm.segment(fc.track, lo, hi)
        assert seg["leadtime"].min() == lo and seg["leadtime"].max() == hi


def test_min_distance_km():
    import geopandas as gpd
    from shapely.geometry import box

    country = gpd.GeoDataFrame(
        geometry=[box(167.0, -18.0, 168.0, -17.0)], crs=4326
    )
    track = parse_forecast_csv(make_csv(ROWS)).track
    # the track runs from -15.0 to -16.0 at ~167.2: ~110 km north of the box
    assert 100 < fcm.min_distance_km(track, country, 120) < 125
    # a lead-time cut that leaves only the first point is further away
    assert fcm.min_distance_km(track, country, 0) > 200


def test_off_grid_issue_time_still_interpolates():
    # pandas 2.x resample().interpolate() gave all-NaN off the :00/:30 clock
    rows = [r.replace(":00:00Z", ":15:00Z") for r in ROWS]
    data = make_csv(rows).replace(b"T00:00:00Z,-15", b"T00:15:00Z,-15")
    data = data.replace(
        b"baseTime=2026-02-01T00:00:00Z", b"baseTime=2026-02-01T00:15:00Z"
    )
    track = parse_forecast_csv(data).track
    assert list(track["leadtime"]) == [0, 6, 12]
    interp = fcm.interpolated(track)
    assert len(interp) == 25 and not interp[["lat", "lon"]].isna().any().any()
    seg = fcm.segment(track, 6, 12)
    assert seg["leadtime"].iloc[0] == 6 and seg["leadtime"].iloc[-1] == 12
    assert not seg["lat"].isna().any()


def test_interpolation_matches_the_design_on_grid():
    from src.utils.wind_buffers import interpolate_track

    track = parse_forecast_csv(SIM.read_bytes()).track
    mine = fcm.interpolated(track)
    design = interpolate_track(track)
    assert len(mine) == len(design)
    for col in ("lat", "lon", "r64_ne", "r34_sw", "leadtime"):
        assert (mine[col].to_numpy() - design[col].to_numpy()).max() < 1e-9


def test_segment_edges_are_exact_and_inclusive():
    track = parse_forecast_csv(SIM.read_bytes()).track
    seg = fcm.segment(track, 72, 120)
    assert seg["leadtime"].min() == 72 and seg["leadtime"].max() == 120
    assert len(seg) == 97
    short = track[track["leadtime"] <= 72]
    one = fcm.segment(short, 72, 120)
    assert list(one["leadtime"]) == [72]  # a forecast ending at 72 h
    assert fcm.segment(track[track["leadtime"] <= 60], 72, 120).empty
