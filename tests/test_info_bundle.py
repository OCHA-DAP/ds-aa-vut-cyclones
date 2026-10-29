import pandas as pd
import pytest

from src.monitoring import info_bundle as ib

T0 = pd.Timestamp("2026-02-03T00:00:00")


def row(source, key, base=T0, name="LOLA", did="2526-07F", lat=-15.0,
        lon=167.0, received=None):  # fmt: skip
    received = (
        received if received is not None else base + pd.Timedelta(hours=1)
    )
    return {
        "key": key,
        "source": source,
        "forecast_id": key,
        "content_sha12": "sha" + key,
        "cyclone_name": name,
        "disturbance_id": did,
        "season": 2026,
        "base_time": base,
        "lat": lat,
        "lon": lon,
        "received_at": received,
        "deadline": received + pd.Timedelta(hours=ib.WAIT_HOURS),
        "status": "pending",
    }


def empty():
    return ib._normalise(pd.DataFrame(columns=ib.COLUMNS))


def status(index, key):
    return index.loc[index["key"] == key, "status"].iloc[0]


@pytest.mark.parametrize(
    "a, b, same",
    [
        ("TC LOLA", "Lola", True),
        ("Severe Tropical Cyclone Lola", "LOLA", True),
        ("08F", "08F", False),  # a number is no name
        ("", "", False),
        ("LOLA", "MAL", False),
    ],
)
def test_storm_names(a, b, same):
    na, nb = ib.storm_name(a), ib.storm_name(b)
    assert (na is not None and na == nb) is same


def test_same_storm_by_id_name_or_distance_and_time():
    a = row("vmhs", "v")
    assert ib.same_storm_and_time(a, row("fms", "f"))  # same id
    assert ib.same_storm_and_time(a, row("fms", "f", did="X", name="Lola"))
    # unnamed, no common id: by distance
    near = row("fms", "f", did="X", name="", lat=-16.0, lon=167.5)
    far = row("fms", "f", did="X", name="", lat=-25.0, lon=175.0)
    unnamed = row("vmhs", "v", did="Y", name="")
    assert ib.same_storm_and_time(unnamed, near)
    assert not ib.same_storm_and_time(unnamed, far)
    # six hours apart: another forecast time
    later = row("fms", "f", base=T0 + pd.Timedelta(hours=6))
    assert not ib.same_storm_and_time(a, later)


def test_second_forecast_sends_both_in_one_email():
    now = T0 + pd.Timedelta(hours=1)
    index, actions = ib.plan(empty(), row("fms", "f"), now)
    assert actions == [] and status(index, "f") == "pending"
    now += pd.Timedelta(minutes=40)
    index, actions = ib.plan(index, row("vmhs", "v", received=now), now)
    assert actions == [("v", "f")]
    assert status(index, "f") == status(index, "v") == "sent"


def test_alone_after_the_deadline_then_both_when_the_partner_is_late():
    now = T0 + pd.Timedelta(hours=1)
    index, _ = ib.plan(empty(), row("vmhs", "v"), now)
    index, actions = ib.plan_flush(index, now + pd.Timedelta(hours=1))
    assert actions == []  # not due yet
    index, actions = ib.plan_flush(index, now + pd.Timedelta(hours=3))
    assert actions == [("v",)] and status(index, "v") == "sent"
    late = now + pd.Timedelta(hours=4)
    index, actions = ib.plan(index, row("fms", "f", received=late), late)
    assert actions == [("f", "v")]  # the late partner: both again


def test_a_forecast_run_also_flushes_other_overdue_emails():
    now = T0 + pd.Timedelta(hours=1)
    index, _ = ib.plan(empty(), row("fms", "old"), now)
    # six hours later, the next RSMC Nadi forecast; the old one never met
    # its VMHS partner
    nxt = T0 + pd.Timedelta(hours=7)
    new = row("fms", "new", base=T0 + pd.Timedelta(hours=6), received=nxt)
    index, actions = ib.plan(index, new, nxt)
    assert actions == [("old",)]
    assert status(index, "new") == "pending"


def test_reissue_replaces_the_waiting_forecast():
    now = T0 + pd.Timedelta(hours=1)
    index, _ = ib.plan(empty(), row("vmhs", "v1"), now)
    now += pd.Timedelta(minutes=20)
    index, _ = ib.plan(index, row("vmhs", "v2", received=now), now)
    assert status(index, "v1") == "superseded"
    now += pd.Timedelta(minutes=20)
    index, actions = ib.plan(index, row("fms", "f", received=now), now)
    assert actions == [("f", "v2")]


def test_two_storms_at_once_pair_by_position():
    now = T0 + pd.Timedelta(hours=1)
    a = row("vmhs", "va", did="A", name="", lat=-14.0, lon=166.0)
    b = row("vmhs", "vb", did="B", name="", lat=-20.0, lon=172.0)
    index, _ = ib.plan(empty(), a, now)
    index, _ = ib.plan(index, b, now)
    f = row("fms", "fb", did="Z", name="", lat=-20.3, lon=172.4)
    index, actions = ib.plan(index, f, now)
    assert actions == [("fb", "vb")]
    assert status(index, "va") == "pending"


def test_the_same_csv_twice_adds_nothing():
    now = T0 + pd.Timedelta(hours=1)
    index, _ = ib.plan(empty(), row("vmhs", "v"), now)
    again, actions = ib.plan(index, row("vmhs", "v"), now)
    assert actions == [] and len(again) == 1


def test_index_round_trips_through_text():
    now = T0 + pd.Timedelta(hours=1)
    index, _ = ib.plan(empty(), row("vmhs", "v"), now)
    out = index.copy()
    for col in ib.TIME_COLS:
        out[col] = out[col].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    back = ib._normalise(out.astype(str))
    assert back.loc[0, "deadline"] == index.loc[0, "deadline"]
    assert ib._due(back, now + pd.Timedelta(hours=3)).all()


VMHS_CTX = {
    "source": "vmhs",
    "forecast_id": "vmhs_lola",
    "content_sha12": "aaa",
    "cyclone_name": "Lola",
    "forecast_display_str": "2026-02-03 11:00 (Vanuatu time)",
    "action_met": True,
    "action_str": "ACTIVATED",
    "action_exp": "20,000",
    "observational_met": False,
    "observational_str": "NOT ACTIVATED",
    "observational_exp": "1,000",
    "n_observed": 2,
    "has_observed": True,
    "observed_display_str": "2026-02-03 11:00 (Vanuatu time)",
    "window_str": "24 to 72 hours",
    "source_name": "VMHS",
    "img_base64_thermometer": None,
    "any_64_knot_exposure": False,
    "any_34_knot_exposure": True,
    "media": [11, 12],
}
FMS_CTX = {
    "source": "fms",
    "forecast_id": "fms_lola",
    "content_sha12": "bbb",
    "cyclone_name": "LOLA",
    "forecast_display_str": "2026-02-03 11:00 (Vanuatu time)",
    "readiness_met": True,
    "readiness_str": "ACTIVATED",
    "readiness_exp": "30,000",
}


def test_render_both_one_or_the_other():
    both = ib.render_info([FMS_CTX, VMHS_CTX], "[TEST] ")
    assert both["subject"].endswith(
        "(READINESS AND ACTION TRIGGERS ACTIVATED)"
    )
    assert both["record_id"] == "vmhs_lola+fms_lola"
    assert both["media"] == [11, 12]
    body = both["body"]
    assert (
        "Readiness, RSMC Nadi forecast" in body and "<b>30,000 people" in body
    )
    assert "Action, VMHS forecast" in body and "Forecast information" in body
    assert "For the readiness trigger only, the RSMC Nadi" in body

    vmhs = ib.render_info([VMHS_CTX])["body"]
    assert "Readiness trigger: <b>not assessed (no RSMC Nadi forecast" in vmhs
    head = vmhs.split("<h2>Reference</h2>")[0]
    assert "Nadi" not in head.replace("no RSMC Nadi forecast", "")

    fms = ib.render_info([FMS_CTX])
    assert (
        fms["media"] == []
        and "(READINESS TRIGGER ACTIVATED)" in fms["subject"]
    )
    assert "Forecast information" not in fms["body"]  # no maps for RSMC Nadi
    assert "Action trigger: <b>not assessed (no VMHS forecast" in fms["body"]
