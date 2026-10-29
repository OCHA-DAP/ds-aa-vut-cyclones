from src.monitoring import info_bundle
from src.monitoring.content import render_template

BASE = {
    "cyclone_name": "Simulated",
    "forecast_display_str": "2026-02-01 11:00 (Vanuatu time)",
    "observed_display_str": "2026-02-01 11:00 (Vanuatu time)",
}


def test_trigger_emails_name_the_right_trigger_and_source():
    r = render_template("readiness.html", BASE)
    assert "readiness trigger" in r and "120 hours" in r and "RSMC" in r
    assert "action trigger" not in r and "72-hour" not in r
    a = render_template("action.html", BASE)
    assert "action trigger" in a and "72-hour" in a and "(VMHS)" in a
    o = render_template("observational.html", BASE)
    assert "observational trigger" in o and "all-clear" in o
    for body in (r, a, o):
        assert "Vanuatu" in body and "Fiji Tropical" not in body


def info(vmhs=True, fms=True, **vmhs_kw):
    """The information email for a VMHS and/or an RSMC Nadi forecast."""
    from tests.test_info_bundle import FMS_CTX, VMHS_CTX

    ctxs = []
    if vmhs:
        ctxs.append(dict(VMHS_CTX, **vmhs_kw))
    if fms:
        ctxs.append(dict(FMS_CTX))
    return info_bundle.render_info(ctxs)["body"]


def test_info_email_shows_each_providers_triggers():
    both = info()
    assert "Readiness trigger: <b>ACTIVATED" in both
    assert "Action trigger: <b>ACTIVATED" in both
    assert "Observational trigger: <b>NOT ACTIVATED" in both
    f = info(vmhs=False)
    assert "Action trigger: <b>not assessed" in f
    v = info(fms=False)
    assert "Readiness trigger: <b>not assessed" in v
    for body in (both, f, v):
        assert "<b>15,000 people</b>" in body and "<b>5,000" not in body


def test_cyclone_name_is_escaped():
    r = render_template("readiness.html", dict(BASE, cyclone_name="<b>x</b>"))
    assert "<b>x</b>" not in r and "&lt;b&gt;x&lt;/b&gt;" in r


def test_info_email_only_claims_the_observed_swath_when_there_is_one():
    kw = dict(any_34_knot_exposure=True, n_observed=1)
    seen = info(has_observed=True, observational_exp="1,439", **kw)
    assert "Observed so far (1 VMHS position up to" in seen
    assert "<b>1,439 people</b>" in seen
    assert "observed so far plus forecast" in seen
    assert "The maps show, faded, the wind swath observed" in seen
    none = info(has_observed=False, observational_exp="", **kw)
    assert "Observed so far (all of Vanuatu): <b>not available</b>" in none
    assert "observed so far plus forecast" not in none
    assert "faded" not in none
    assert "The maps show the forecast wind swath along" in none
    assert "people</b>" not in none.split("Observed so far")[1][:80]


def test_vmhs_is_the_only_authority_and_nadi_only_for_readiness():
    official = "all official information on tropical cyclones in Vanuatu"
    # VMHS on its own: only VMHS, no RSMC Nadi outside the framework
    # reference
    v = info(fms=False)
    body = v.split("<h2>Reference</h2>")[0]
    assert official in body and "Nadi" not in body
    assert "met.gov.fj" not in v
    for t in ("action.html", "observational.html"):
        e = render_template(t, BASE)
        assert "national authority" in e and "Nadi" not in e
    # with RSMC Nadi: readiness only, VMHS the official source
    for f in (info(), info(vmhs=False)):
        assert official in f and "only for its readiness trigger" in f
        assert "met.gov.fj" not in f
    r = render_template("readiness.html", BASE)
    assert "only for this readiness trigger" in r
    assert "not an official forecast for Vanuatu" in r and "(VMHS)" in r
