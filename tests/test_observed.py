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


def test_records_are_separate_per_source_and_mode():
    names = {
        observed.record_blob(test, source)
        for test in (True, False)
        for source in ("fms", "vmhs")
    }
    assert len(names) == 4
    # the VMHS record keeps the name it had
    assert observed.record_blob(False).endswith(
        "/monitoring/vmhs_observed_points.csv"
    )
    import pytest

    with pytest.raises(ValueError):
        observed.record_blob(False, "other")


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


def test_analysis_point_tolerates_a_first_point_near_issue():
    fc = parse_forecast_csv(make_csv(ROWS))
    late = fc.track.copy()
    late["leadtime"] = late["leadtime"] + 3
    fc.track = late
    assert len(observed.analysis_point(fc, "a")) == 1
    late["leadtime"] = late["leadtime"] + 3  # first point 6 h after issue
    fc.track = late
    import pytest

    with pytest.raises(ValueError):
        observed.analysis_point(fc, "a")


def test_keys_match_whatever_the_dtypes():
    fc = parse_forecast_csv(make_csv(ROWS))
    pt = observed.analysis_point(fc, "a")
    as_text = pt.astype(str)  # what a dtype=str load gives back
    out = observed.add_point(as_text, observed.analysis_point(fc, "b"))
    assert len(out) == 1 and out.iloc[0]["content_sha12"] == "b"
    numeric_id = pt.assign(disturbance_id="07").astype(str)
    tr = observed.observed_track(
        observed.add_point(numeric_id, pt), "07", 2026
    )
    assert len(tr) == 1


def test_observed_track_stops_at_the_forecast_issue_time():
    fc = parse_forecast_csv(make_csv(ROWS))
    pts = observed.add_point(
        pd.DataFrame(columns=observed.COLUMNS),
        observed.analysis_point(fc, "a"),
    )
    later = observed.analysis_point(fc, "b").assign(
        valid_time=pd.Timestamp("2026-02-01T12:00:00")
    )
    pts = observed.add_point(pts, later)
    assert len(observed.observed_track(pts, "2526-07F", 2026)) == 2
    upto = observed.observed_track(
        pts, "2526-07F", 2026, until=pd.Timestamp("2026-02-01T06:00:00")
    )
    assert list(upto["valid_time"]) == [pd.Timestamp("2026-02-01T00:00:00")]


def test_own_analysis_point_after_issue_time_is_kept():
    # first row 1 h after issue: accepted as the analysis point, so the
    # forecast's own observed track must include it
    fc = parse_forecast_csv(make_csv(ROWS))
    late = fc.track.copy()
    late["valid_time"] = late["valid_time"] + pd.Timedelta(hours=1)
    late["leadtime"] = late["leadtime"] + 1
    fc.track = late
    pt = observed.analysis_point(fc, "a")
    pts = observed.add_point(pd.DataFrame(columns=observed.COLUMNS), pt)
    tr = observed.observed_track(
        pts, "2526-07F", 2026, until=observed.cutoff(fc, pt)
    )
    assert len(tr) == 1
    # a later forecast's point stays out
    later = pt.assign(valid_time=pt["valid_time"] + pd.Timedelta(hours=6))
    pts = observed.add_point(pts, later)
    tr = observed.observed_track(
        pts, "2526-07F", 2026, until=observed.cutoff(fc, pt)
    )
    assert len(tr) == 1
