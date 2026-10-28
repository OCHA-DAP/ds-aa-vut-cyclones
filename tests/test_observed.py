import pandas as pd

from src.monitoring import observed
from src.monitoring.forecast import parse_forecast_csv
from tests.test_forecast import ROWS, make_csv


def fc_at(base, lat):
    rows = [r.replace("2026-02-01T00:00:00Z", base) for r in ROWS[:1]]
    rows = [rows[0].replace("-15.0", str(lat))] + ROWS[1:]
    data = make_csv(rows)
    return parse_forecast_csv(
        data.replace(b"2026-02-01T00:00:00Z", base.encode(), 1)
    )


def test_analysis_point_is_lead_time_zero():
    fc = parse_forecast_csv(make_csv(ROWS))
    pt = observed.analysis_point(fc, "abc")
    assert len(pt) == 1
    assert pt.iloc[0]["lat"] == -15.0
    assert pt.iloc[0]["disturbance_id"] == "2526-07F"
    assert list(pt.columns) == observed.COLUMNS


def test_track_builds_up_and_reissue_replaces():
    pts = pd.DataFrame(columns=observed.COLUMNS)
    fc = parse_forecast_csv(make_csv(ROWS))
    pts = observed.add_point(pts, observed.analysis_point(fc, "a"))
    pts = observed.add_point(pts, observed.analysis_point(fc, "b"))
    assert len(pts) == 1 and pts.iloc[0]["content_sha12"] == "b"
    other = observed.analysis_point(fc, "c").assign(
        valid_time=pd.Timestamp("2026-02-01T06:00:00"), lat=-15.4
    )
    pts = observed.add_point(pts, other)
    tr = observed.observed_track(pts, "2526-07F", 2026)
    assert list(tr["lat"]) == [-15.0, -15.4]
    assert observed.observed_track(pts, "2526-08F", 2026).empty
    assert observed.observed_track(pts, "2526-07F", 2025).empty


def test_test_and_real_records_are_separate():
    assert observed.record_blob(True) != observed.record_blob(False)


def test_track_is_numeric_from_an_empty_first_record():
    fc = parse_forecast_csv(make_csv(ROWS))
    pts = observed.add_point(
        pd.DataFrame(columns=observed.COLUMNS),
        observed.analysis_point(fc, "a"),
    )
    later = observed.analysis_point(fc, "b").assign(
        valid_time=pd.Timestamp("2026-02-01T06:00:00")
    )
    tr = observed.observed_track(
        observed.add_point(pts, later), "2526-07F", 2026
    )
    assert tr["lon"].dtype == float and tr["r64_ne"].dtype == float
