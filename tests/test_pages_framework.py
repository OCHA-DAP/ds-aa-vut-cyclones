"""The public pages must show the trigger as the framework document has it.

docs/trigger/ is linked from the published framework (Annex 2, "Evaluation of
trigger performance"), and its storm table and Trigger Mechanism Statistics
are drawn from docs/forecast-check/data/{rp,hist}.json by
docs/assets/historical.js. These tests compare them with the figures printed
in the framework (endorsed by the ERC on 15 October 2026), twice: the data,
through the same selection in Python, and the page script itself, run under
node, so neither regenerated data nor a change to the script can silently
change them.
"""

import html
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
DATA = DOCS / "forecast-check" / "data"
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

    act = sorted({s["season"] for s in rp["scored_storms"] if s["peakA"] >= T})
    obs = sorted(rp["observational"]["activated_seasons"])
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


# What historical.js draws, run under node: the rows of the storm table and
# the text of the statistics panel.
NODE_SCRIPT = """
const fs = require("fs"), vm = require("vm");
const [js, rp, hist] = process.argv.slice(1);
const ctx = { window: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(js, "utf8"), ctx);
const R = ctx.window.VutRecord;
const read = (f) => JSON.parse(fs.readFileSync(f, "utf8"));
console.log(JSON.stringify({
  T: R.T,
  table: R.annexTable(read(rp), read(hist)),
  panel: R.tmsPanel(read(rp)),
}));
"""


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


@pytest.fixture(scope="module")
def drawn():
    node = shutil.which("node")
    if node is None:
        if os.environ.get("REQUIRE_NODE"):
            pytest.fail("node is needed to run docs/assets/historical.js")
        pytest.skip("node not installed")
    out = subprocess.run(
        [
            node,
            "-e",
            NODE_SCRIPT,
            str(DOCS / "assets" / "historical.js"),
            str(DATA / "rp.json"),
            str(DATA / "hist.json"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_the_page_script_draws_annex_2(drawn):
    assert drawn["T"] == T
    body = drawn["table"].split("<tbody>")[1]
    rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)
    got = [
        [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        for r in rows
    ]

    def cell(v):
        if v is None:
            return "no record"
        return "no record*" if v == "*" else f"{v:,}"

    want = [
        [name, cell(f), f"{o:,}", "\u2713" if cerf else "\u2014", f"{a:,}"]
        for name, f, o, cerf, a in ANNEX_2
    ]
    assert got == want
    # the cells that met the trigger are the ones marked
    met = [
        [
            text(c)
            for c in re.findall(r'<td class="num met">(.*?)</td>', r, re.S)
        ]
        for r in rows
    ]
    assert met == [
        [f"{v:,}" for v in (f, o) if isinstance(v, int) and v >= T]
        for _, f, o, _, _ in ANNEX_2
    ]


def test_the_page_script_draws_the_statistics(drawn):
    panel = text(drawn["panel"])
    act_years, act_pct, act = STATS["action"]
    obs_years, obs_pct, obs = STATS["observational"]
    all_years, all_pct, spend = STATS["overall"]
    for want in [
        f"Analysis years: {STATS['years']}",
        f"Action {act_years} {act_pct} {', '.join(map(str, act))}, +1 assumed*",
        f"Observational {obs_years} {obs_pct} {', '.join(map(str, obs))}",
        f"Overall return period \u2264 {all_years}",
        f"Overall probability of activation \u2265 {all_pct}",
        f"Average total spending per year \u2265 {spend}",
        # the panel's footnote, word for word
        "*One assumed action activation in 2008 (Gene) or 2011 (Atu): both "
        "storms' hurricane-force winds reached or nearly reached the trigger "
        "provinces, but no forecast records survive for 2006\u201311. The "
        "readiness window is not assessed (too little archived long-range "
        "data), so the overall return period is reported as \u2264 the "
        "computed value. Pre-arranged financing: $1.5M, disbursed in full on "
        "either an action or an observational activation.",
    ]:
        assert want in panel


def test_the_trigger_page_defines_the_endorsed_trigger():
    page = (DOCS / "trigger" / "index.html").read_text()
    table = re.search(r'<table class="def">(.*?)</table>', page, re.S).group(1)
    rows = [text(r) for r in re.findall(r"<tr>(.*?)</tr>", table, re.S)]
    assert rows == [
        "Trigger Lead time Wind speed Number of people exposed Provinces "
        "Data source",
        "Readiness 72 to 120 hours \u2265 64 knots \u2265 15,000 people "
        "Shefa, Sanma, Tafea RSMC Nadi extended track forecast",
        "Action 24 to 72 hours VMHS track forecast",
        "Observational n/a All of Vanuatu VMHS observed track",
    ]
