from src.monitoring.content import render_template

BASE = {
    "cyclone_name": "Simulated",
    "forecast_display_str": "2026-02-01 11:00 (Vanuatu time)",
    "observed_display_str": "2026-02-01 11:00 (Vanuatu time)",
}


def test_trigger_emails_name_the_right_trigger_and_source():
    r = render_template("readiness.html", BASE)
    assert "readiness trigger" in r and "120-hour" in r and "RSMC" in r
    assert "action trigger" not in r and "72-hour" not in r
    a = render_template("action.html", BASE)
    assert "action trigger" in a and "72-hour" in a and "(VMHS)" in a
    o = render_template("observational.html", BASE)
    assert "observational trigger" in o and "all-clear" in o
    for body in (r, a, o):
        assert "Vanuatu" in body and "Fiji Tropical" not in body


def info(source, **kw):
    v = dict(
        BASE,
        source=source,
        readiness_str="NOT ACTIVATED",
        action_str="ACTIVATED",
        observational_str="NOT ACTIVATED",
        readiness_exp="0",
        early_exp="0",
        action_exp="20,000",
        observational_exp="0",
        n_observed=1,
        window_str="24 to 72 hours",
        img_base64_thermometer=None,
        forecast_id="x",
        any_64_knot_exposure=False,
        any_34_knot_exposure=False,
    )
    v.update(kw)
    return render_template("informational.html", v)


def test_info_email_shows_only_the_sources_triggers():
    f = info("fms")
    assert "Readiness trigger: <b>" in f and "Action trigger: <b>" not in f
    v = info("vmhs")
    assert "Action trigger: <b>ACTIVATED" in v
    assert "Observational trigger: <b>" in v
    assert "Readiness trigger: <b>" not in v
    for body in (f, v):
        assert "<b>15,000 people</b>" in body and "<b>5,000" not in body


def test_cyclone_name_is_escaped():
    r = render_template("readiness.html", dict(BASE, cyclone_name="<b>x</b>"))
    assert "<b>x</b>" not in r and "&lt;b&gt;x&lt;/b&gt;" in r


def test_info_email_only_claims_the_observed_swath_when_there_is_one():
    kw = dict(any_34_knot_exposure=True, source_name="VMHS")
    seen = info("vmhs", has_observed=True, observational_exp="1,439", **kw)
    assert "Observed so far (1 VMHS position up to" in seen
    assert "<b>1,439 people</b>" in seen
    assert "observed so far plus forecast" in seen
    assert "The maps show, faded, the wind swath observed" in seen
    none = info("vmhs", has_observed=False, observational_exp="", **kw)
    assert "Observed so far (all of Vanuatu): <b>not available</b>" in none
    assert "observed so far plus forecast" not in none
    assert "faded" not in none
    assert "The maps show the forecast wind swath along" in none
    assert "people</b>" not in none.split("Observed so far")[1][:80]
