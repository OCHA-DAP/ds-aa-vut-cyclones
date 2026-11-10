"""The public pages must show the trigger as the framework document has it.

docs/trigger/ is linked from the published framework (Annex 2, "Evaluation of
trigger performance"), and its storm table and Trigger Mechanism Statistics
are drawn from docs/forecast-check/data/{rp,hist}.json by
docs/assets/historical.js. These tests repeat that selection in Python and
compare it with the figures printed in the framework (endorsed by the ERC on
15 October 2026), so regenerating the data cannot silently change them.
"""

import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "docs" / "forecast-check" / "data"
T = 15_000
ASSUMED = {"GENE2008", "ATU2011"}
ROWS = 20

# Annex 2 of the framework, row by row: storm, 64 kt forecast exposure
# (action window, AOI; None = "no record"; "*" = no record, assumed
# activation), 64 kt observed exposure (whole country), CERF allocation,
# EM-DAT total affected.
ANNEX_2 = [
    ("Kevin 2023", 49_587, 151_930, True, 251_356),
    ("Judy 2023", 56_615, 172_264, True, 251_346),
    ("Pam 2015", 2_478, 46_508, True, 188_000),
    ("Harold 2020", 45_771, 72_516, True, 130_120),
    ("Lola 2024", 1_118, 41_970, True, 91_000),
    ("Vania 2011", None, 0, False, 32_000),
    ("Lusi 2014", 0, 0, False, 20_006),
    ("Donna 2017", 7_967, 571, False, 2_564),
    ("Jasmine 2012", 50_017, 0, False, 0),
    ("Hola 2018", 0, 0, False, 0),
    ("Becky 2007", None, 0, False, 0),
    ("Cook 2017", 0, 0, False, 0),
    ("Atu 2011", "*", 0, False, 0),
    ("Irene 2023", 0, 0, False, 0),
    ("Dovi 2022", 0, 0, False, 0),
    ("Tam 2025", None, 0, False, 0),
    ("Unnamed 2024", None, 0, False, 0),
    ("Zena 2016", 0, 0, False, 0),
    ("Funa 2008", None, 0, False, 0),
    ("Gene 2008", "*", 0, False, 0),
]

# The Trigger Mechanism Statistics panel (section III.2).
STATS = {
    "years": "2006-2025",
    "action": ("5.3 years", "19%", [2012, 2020, 2023]),  # + 1 assumed
    "observational": ("5.3 years", "19%", [2015, 2020, 2023, 2024]),
    "overall": ("3.5 years", "29%", "$0.4M"),
}


def load(name):
    return json.loads((DATA / name).read_text())


def js_round(x):
    """Math.round, which rounds halves up (Python's round() does not)."""
    return math.floor(x + 0.5)


def years(v):
    return f"{js_round(v * 10) / 10:.1f}".removesuffix(".0") + " years"


def pct(v):
    return f"{js_round(100 / v)}%"


def annex_rows(rp, hist):
    """historical.js annexRows()."""
    scored = {
        s["name"].upper() + str(s["season"]): s for s in rp["scored_storms"]
    }
    rows = []
    for h in hist["storms"]:
        key = h["name"].upper() + str(h["season"])
        s = scored.get(key)
        rows.append(
            {
                "name": h["name"][0] + h["name"][1:].lower(),
                "season": h["season"],
                "peakA": s["peakA"] if s else None,
                "obs": s["obs"] if s else 0,
                "cerf": h["cerf"],
                "affected": h["affected"],
                "exp34": h.get("exp34") or 0,
                "assumed": key in ASSUMED,
            }
        )
    shown = [
        r
        for r in rows
        if (r["peakA"] or 0) > 0
        or r["obs"] > 0
        or r["cerf"]
        or r["affected"] > 0
        or r["assumed"]
    ]
    rest = sorted(
        (r for r in rows if r not in shown), key=lambda r: -r["exp34"]
    )
    rows = shown + rest[: max(0, ROWS - len(shown))]
    return sorted(
        rows,
        key=lambda r: (
            -r["affected"],
            -r["obs"],
            -(r["peakA"] or 0),
            -r["exp34"],
        ),
    )


def test_the_data_is_for_the_endorsed_threshold():
    rp = load("rp.json")
    assert rp["threshold"] == T
    assert (rp["first_season"], rp["last_season"], rp["n_seasons"]) == (
        2006,
        2025,
        20,
    )


def test_storm_table_is_annex_2():
    got = []
    for r in annex_rows(load("rp.json"), load("hist.json")):
        forecast = "*" if r["assumed"] else r["peakA"]
        got.append(
            (
                f"{r['name']} {r['season']}",
                forecast,
                r["obs"],
                r["cerf"],
                r["affected"],
            )
        )
    assert got == ANNEX_2


def test_statistics_are_the_frameworks():
    rp = load("rp.json")
    n, extra = rp["n_seasons"], rp["combined"]["extra_default"]

    def seasons(f):
        return sorted({s["season"] for s in rp["scored_storms"] if f(s)})

    act = seasons(lambda s: s["peakA"] >= T)
    obs = seasons(lambda s: s["obs"] >= T)
    rp_act = (n + 1) / (len(act) + extra)
    rp_obs = (n + 1) / len(obs)
    rp_all = (n + 1) / (len(set(act) | set(obs)) + extra)

    assert f"{rp['first_season']}-{rp['last_season']}" == STATS["years"]
    assert extra == 1
    assert (years(rp_act), pct(rp_act), act) == STATS["action"]
    assert (years(rp_obs), pct(rp_obs), obs) == STATS["observational"]
    assert (years(rp_all), pct(rp_all), f"${1.5 / rp_all:.1f}M") == STATS[
        "overall"
    ]
