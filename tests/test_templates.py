from src.monitoring import info_bundle
from src.monitoring.content import render_template

BASE = {
    "cyclone_name": "Simulated",
    "forecast_time_str": "11:00 on 1 February 2026",
    "observed_time_str": "11:00 on 1 February 2026",
}


def test_trigger_emails_follow_the_framework_draft_messages():
    r = render_template("readiness.html", BASE)
    assert (
        "The readiness trigger of the coordinated anticipatory action pilot "
        "for tropical cyclones in Vanuatu has been reached in line with the "
        "120-hour forecast for Cyclone Simulated produced at 11:00 on 1 "
        "February 2026 (Vanuatu local time) by the Regional Specialized "
        "Meteorological Centre (RSMC) Nadi, operated by the Fiji "
        "Meteorological Service."
    ) in " ".join(r.split())
    a = render_template("action.html", BASE)
    assert (
        "72-hour forecast for Cyclone Simulated produced at 11:00 on 1 "
        "February 2026 (Vanuatu local time) by the Vanuatu Meteorology and "
        "Hydro-Meteorological Services (VMHS)."
    ) in " ".join(a.split())
    for body in (r, a):
        assert (
            "All activities in line with the framework document should now "
            "commence. OCHA may convene a meeting of the participating "
            "partners."
        ) in " ".join(body.split())
    o = render_template("observational.html", BASE)
    assert "observational trigger" in o and "all-clear" in o
    for body in (r, a, o):
        assert "Dear NDMO Director, dear Resident Coordinator" in body
        assert "Fiji Tropical" not in body


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
        assert "Nadi" not in render_template(t, BASE)
    # with RSMC Nadi: readiness only, VMHS the official source
    for f in (info(), info(vmhs=False)):
        assert official in f and "For the readiness trigger only" in f
        assert "extended track forecast of the Regional" not in f
        assert "met.gov.fj" not in f
