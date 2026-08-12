"""Return-period breakdown for the framework triggers -> data/rp.json.

Combines, at the working trigger (>=10,000 people at 64 kt, 10-min):

- the observational leg (national scope), scored over the full 2005-2025
  record from IBTrACS swaths;
- the action leg (24-72 h forecast window, AOI scope), scored where JTWC
  forecast decks exist: 2005 (UCAR archive) and 2012-2025 (VMGD archive +
  UCAR 2025);
- the six unscorable forecast seasons (2006-2011) are handled with a
  user-selectable "assumed extra activated seasons" (default 1) on the
  page; this script ships the context (observed swath distances) and the
  per-threshold scored season counts, and the page computes deterministic
  Weibull RPs from them.

Kerry 2005 is entered as a scored constant: its 2005-01-05 00Z JTWC cycle
puts 8,260 people in the AOI action window at the V_EQ contour (64 kt
10-min = ~73 kt 1-min; 14,591 in raw 1-min terms — deck ash082005.dat,
preserved at blob raw/jtwc/ucar_adecks_sh_2003-2011_2025.zip). The
observed storm passed 586 km from the AOI — a false alarm, and a caution
that the unscored 2006-11 seasons plausibly held similar events (hence the
page's assumed-extra-seasons control, default 1).

Run from the repo root:
    uv run python exploration/make_rp_summary.py
"""

import json
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import MultiPolygon, Polygon

from src.constants import ADM1_AOI_PCODES, FJI_CRS
from src.datasources import codab

DATA = Path("docs/forecast-check/data")
T = 10000
FIRST, LAST = 2005, 2025
N_SEASONS = LAST - FIRST + 1

# directly-scored action-leg result outside the VMGD-archive era
KERRY_2005 = {
    "name": "Kerry",
    "season": 2005,
    "sid": "2005003S09177",
    "peakA": 8260,
    "obs": 0,
}

# forecast decks exist for these seasons (UCAR 2005+2025, VMGD 2012-2024)
SCORED_FCST_SEASONS = {2005} | set(range(2012, 2026))


def rings_to_3832(rings):
    if not rings:
        return None
    polys = [Polygon(r) for r in rings if len(r) >= 4]
    if not polys:
        return None
    g = shapely.make_valid(MultiPolygon(polys) if len(polys) > 1 else polys[0])
    return gpd.GeoSeries([g], crs=FJI_CRS).to_crs(3832).iloc[0]


def main():
    core = json.load(open(DATA / "core.json"))
    hist = json.load(open(DATA / "hist.json"))
    adm2 = codab.load_codab_from_blob(admin_level=2)
    aoi_land = (
        adm2[adm2.ADM1_PCODE.isin(ADM1_AOI_PCODES)]
        .to_crs(3832)
        .geometry.union_all()
    )

    # ---- observational leg: national >= T, full record -------------------
    obs_hits = [
        {
            "name": s["name"].title(),
            "season": s["season"],
            "exp": int(s["obs"].get("64", 0)),
        }
        for s in core["storms"]
        if int(s["obs"].get("64", 0)) >= T and FIRST <= s["season"] <= LAST
    ]
    obs_seasons = sorted({h["season"] for h in obs_hits})

    # ---- action leg: scored storms -------------------------------------
    def peak_a(s):
        return max(int(c.get("expA64", 0)) for c in s["cycles"])

    act_hits = [
        {
            "name": s["name"].title(),
            "season": s["season"],
            "peakA": peak_a(s),
            "obs": int(s["obs"].get("64", 0)),
        }
        for s in core["storms"]
        if peak_a(s) >= T and FIRST <= s["season"] <= LAST
    ]
    if KERRY_2005["peakA"] >= T:
        act_hits.append(dict(KERRY_2005, peakA=KERRY_2005["peakA"]))
    act_hits.sort(key=lambda h: h["season"])
    act_seasons = sorted({h["season"] for h in act_hits})
    false_alarms = [h for h in act_hits if h["obs"] < T]

    combined_seasons = sorted(set(obs_seasons) | set(act_seasons))

    # ---- unscored forecast seasons: per-storm P from observed geometry --
    unscored = sorted(
        se for se in range(FIRST, LAST + 1) if se not in SCORED_FCST_SEASONS
    )
    n_known = len(combined_seasons)

    # ---- threshold sweep: scored activated-season count per threshold --
    sweep_storms = [
        {
            "season": s["season"],
            "peakA": peak_a(s),
            "obs": int(s["obs"].get("64", 0)),
        }
        for s in core["storms"]
        if FIRST <= s["season"] <= LAST
    ] + [KERRY_2005]
    gap_list = []
    for s in hist["storms"]:
        if s["season"] not in unscored:
            continue
        g = json.load(open(DATA / "obsgeom" / f"{s['sid']}.json"))
        geom = rings_to_3832(g["rings"].get("64"))
        if geom is not None:
            gap_list.append(
                {
                    "name": s["name"].title(),
                    "season": s["season"],
                    "dist_km": round(geom.distance(aoi_land) / 1000),
                    "obs_aoi": int(s["exp64"]),
                }
            )
    gap_list.sort(key=lambda g: g["dist_km"])

    sweep = []
    for thr in range(1000, 81001, 1000):
        seasons = {
            s["season"]
            for s in sweep_storms
            if s["peakA"] >= thr or s["obs"] >= thr
        }
        n = max(len(seasons), 1)
        sweep.append(
            {"t": thr, "n": n, "scored": round((N_SEASONS + 1) / n, 2)}
        )

    # ---- storms without forecast decks, for the explorer scatter --------
    # national V_EQ observed exposure so every storm with nonzero 64-kt
    # exposure appears on the plot (hollow markers, no forecast value)
    affected_by_sid = {s["sid"]: s["affected"] for s in hist["storms"]}
    core_sids = {s["sid"] for s in core["storms"] if s.get("sid")}
    nodeck = [
        s
        for s in hist["storms"]
        if s["sid"] not in core_sids and s["sid"] != KERRY_2005["sid"]
    ]
    extra_storms = []
    if nodeck:
        from make_forecast_check_data import (
            load_observed_veq_swaths,
            national_exposure_context,
        )

        swaths = load_observed_veq_swaths({s["sid"] for s in nodeck})
        if swaths:
            expo_nat = national_exposure_context()
            for s in nodeck:
                geom = swaths.get(s["sid"])
                if geom is None:
                    continue
                gw = gpd.GeoSeries([geom], crs=3832).to_crs(FJI_CRS).iloc[0]
                obs = expo_nat(gw)
                if obs > 0:
                    extra_storms.append(
                        {
                            "name": s["name"].title(),
                            "season": s["season"],
                            "obs": obs,
                            "affected": s["affected"],
                        }
                    )
    print("extra (no-deck) storms with nonzero V_EQ obs:", extra_storms)

    out = {
        "threshold": T,
        "first_season": FIRST,
        "last_season": LAST,
        "n_seasons": N_SEASONS,
        "observational": {
            "scored_seasons": N_SEASONS,
            "storms": obs_hits,
            "n_storms": len(obs_hits),
            "activated_seasons": obs_seasons,
            "rp_seasons": round((N_SEASONS + 1) / len(obs_seasons), 1),
        },
        "action": {
            "scored_seasons": sorted(SCORED_FCST_SEASONS),
            "n_scored_seasons": len(SCORED_FCST_SEASONS),
            "storms": act_hits,
            "n_storms": len(act_hits),
            "activated_seasons": act_seasons,
            "false_alarms": [
                {"name": h["name"], "season": h["season"]}
                for h in false_alarms
            ],
        },
        "combined": {
            "scored_activated_seasons": combined_seasons,
            "rp_scored_only": round((N_SEASONS + 1) / n_known, 1),
            "unscored_seasons": unscored,
            "extra_default": 1,
            "sweep": sweep,
            "gap_list": gap_list,
        },
        "scored_storms": [
            {
                "name": (s["name"].title() if s.get("name") else "?"),
                "season": s["season"],
                "peakA": s["peakA"],
                "obs": s["obs"],
                "cerf": bool(s.get("cerf")),
                "affected": affected_by_sid.get(s.get("sid"), 0),
            }
            for s in (
                [
                    {
                        "name": x["name"],
                        "season": x["season"],
                        "sid": x.get("sid"),
                        "peakA": peak_a(x),
                        "obs": int(x["obs"].get("64", 0)),
                        "cerf": x.get("cerf"),
                    }
                    for x in core["storms"]
                    if FIRST <= x["season"] <= LAST
                ]
                + [KERRY_2005]
            )
        ],
        "extra_storms": extra_storms,
    }
    with open(DATA / "rp.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(
        json.dumps(
            {k: v for k, v in out["combined"].items() if k != "sweep"},
            indent=1,
        )
    )
    print("observational:", obs_seasons, "| action:", act_seasons)
    print(f"wrote {DATA/'rp.json'}")


if __name__ == "__main__":
    main()
