"""Return-period breakdown for the framework triggers -> data/rp.json.

Combines, at the working trigger (>=5,000 people at 64 kt):

- the observational leg (national scope), scored over the full 2005-2025
  record from IBTrACS swaths;
- the action leg (24-72 h forecast window, AOI scope), scored where JTWC
  forecast decks exist: 2005 (UCAR archive) and 2012-2025 (VMGD archive +
  UCAR 2025);
- an estimate for the unscorable forecast seasons (2006-2011), from a
  logistic P(action fires | observed 64-kt swath miss distance) calibrated
  on the scored storms, Monte-Carlo'd into an RP range.

Kerry 2005 is entered as a scored constant: its 2005-01-05 00Z JTWC cycle
puts 14,591 people in the AOI action window (deck ash082005.dat, preserved
at blob raw/jtwc/ucar_adecks_sh_2003-2011_2025.zip). The observed storm
passed 586 km from the AOI — a false alarm, and the reason the logistic
P's below are treated as floors: 2005-era forecast errors exceed the
2012-25 errors the curve is calibrated on.

Run from the repo root:
    uv run python exploration/make_rp_summary.py
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon

from src.constants import ADM1_AOI_PCODES, FJI_CRS
from src.datasources import codab

DATA = Path("docs/forecast-check/data")
T = 5000
FIRST, LAST = 2005, 2025
N_SEASONS = LAST - FIRST + 1

# directly-scored action-leg result outside the VMGD-archive era
KERRY_2005 = {"name": "Kerry", "season": 2005, "peakA": 14591, "obs": 0}

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
    act_hits.append(dict(KERRY_2005, peakA=KERRY_2005["peakA"]))
    act_hits.sort(key=lambda h: h["season"])
    act_seasons = sorted({h["season"] for h in act_hits})
    false_alarms = [h for h in act_hits if h["obs"] < T]

    combined_seasons = sorted(set(obs_seasons) | set(act_seasons))

    # ---- logistic calibration: P(action fires | obs miss distance) ------
    D_cal, y_cal = [], []
    for s in core["storms"]:
        if not (FIRST <= s["season"] <= LAST):
            continue
        g = json.load(open(DATA / "geom" / f"{s['id']}.json"))
        geom = rings_to_3832(g.get("obs_buf64"))
        D = geom.distance(aoi_land) / 1000 if geom is not None else 3000.0
        D_cal.append(min(D, 3000.0))
        y_cal.append(1 if peak_a(s) >= T else 0)
    D_cal, y_cal = np.array(D_cal), np.array(y_cal)

    def nll(d0, sc):
        z = np.clip((D_cal - d0) / sc, -50, 50)
        P = np.clip(1 / (1 + np.exp(z)), 1e-9, 1 - 1e-9)
        return -(y_cal * np.log(P) + (1 - y_cal) * np.log(1 - P)).sum()

    _, d0, sc = min(
        (
            (nll(d0, sc), d0, sc)
            for d0 in np.arange(10, 301, 2)
            for sc in np.arange(5, 151, 2.5)
        ),
        key=lambda t: t[0],
    )

    def pfire(D):
        return float(1 / (1 + np.exp(np.clip((D - d0) / sc, -50, 50))))

    # ---- unscored forecast seasons: per-storm P from observed geometry --
    unscored = sorted(
        se for se in range(FIRST, LAST + 1) if se not in SCORED_FCST_SEASONS
    )
    gap_storms = []
    for s in hist["storms"]:
        if s["season"] not in unscored:
            continue
        g = json.load(open(DATA / "obsgeom" / f"{s['sid']}.json"))
        geom = rings_to_3832(g["rings"].get("64"))
        if geom is None:
            continue  # never reached 64 kt -> cannot fire a 64-kt trigger
        D = geom.distance(aoi_land) / 1000
        p = pfire(D)
        if p >= 0.01:
            gap_storms.append(
                {
                    "name": s["name"].title(),
                    "season": s["season"],
                    "dist_km": round(D),
                    "p": round(p, 2),
                }
            )
    p_season = {}
    for se in unscored:
        ps = [g["p"] for g in gap_storms if g["season"] == se]
        p = 1 - float(np.prod([1 - x for x in ps])) if ps else 0.0
        if p >= 0.01:
            p_season[se] = round(p, 2)

    # ---- combined RP: exact enumeration over the gap-season Bernoullis --
    def rp_quantiles(n_known, probs):
        """RP distribution of (N+1)/(n_known + extra), extra = sum of
        independent Bernoulli(probs). Exact; returns (q10, med, q90)."""
        dist = {0: 1.0}
        for p in probs:
            nxt = {}
            for k, w in dist.items():
                nxt[k] = nxt.get(k, 0) + w * (1 - p)
                nxt[k + 1] = nxt.get(k + 1, 0) + w * p
            dist = nxt
        outcomes = sorted(
            ((N_SEASONS + 1) / (n_known + k), w) for k, w in dist.items()
        )
        qs = []
        for alpha in (0.10, 0.50, 0.90):
            cum = 0.0
            for v, w in outcomes:
                cum += w
                if cum >= alpha - 1e-12:
                    qs.append(v)
                    break
        return qs

    n_known = len(combined_seasons)
    q10, med, q90 = rp_quantiles(n_known, list(p_season.values()))

    # ---- threshold sweep: combined RP as a function of the threshold ----
    # per-storm scored values (action peak with Kerry, obs national) and
    # gap-storm distances stay fixed; the logistic is refit per distinct
    # calibration outcome vector (cached — ~10 distinct fits)
    sweep_storms = [
        {
            "season": s["season"],
            "peakA": peak_a(s),
            "obs": int(s["obs"].get("64", 0)),
        }
        for s in core["storms"]
        if FIRST <= s["season"] <= LAST
    ] + [KERRY_2005]
    gap_D = {}
    for s in hist["storms"]:
        if s["season"] not in unscored:
            continue
        g = json.load(open(DATA / "obsgeom" / f"{s['sid']}.json"))
        geom = rings_to_3832(g["rings"].get("64"))
        if geom is not None:
            gap_D.setdefault(s["season"], []).append(
                geom.distance(aoi_land) / 1000
            )

    cal_peaks = [
        peak_a(s) for s in core["storms"] if FIRST <= s["season"] <= LAST
    ]
    fit_cache = {}

    def logi_nll(ya, dd, ss):
        z = np.clip((D_cal - dd) / ss, -50, 50)
        P = np.clip(1 / (1 + np.exp(z)), 1e-9, 1 - 1e-9)
        return -(ya * np.log(P) + (1 - ya) * np.log(1 - P)).sum()

    def fit_for(thr):
        y = tuple(1 if p >= thr else 0 for p in cal_peaks)
        if y not in fit_cache:
            if sum(y) == 0:
                fit_cache[y] = None  # no positives: no basis to estimate
            else:
                ya = np.array(y)
                fit_cache[y] = min(
                    (
                        (dd, ss)
                        for dd in np.arange(10, 301, 4)
                        for ss in np.arange(5, 151, 5)
                    ),
                    key=lambda t: logi_nll(ya, *t),
                )
        return fit_cache[y]

    sweep = []
    for thr in range(1000, 81001, 1000):
        seasons = {
            s["season"]
            for s in sweep_storms
            if s["peakA"] >= thr or s["obs"] >= thr
        }
        fit = fit_for(thr)
        probs = []
        if fit is not None:
            fd0, fsc = fit
            for se, Ds in gap_D.items():
                pse = 1 - np.prod(
                    [
                        1 - 1 / (1 + np.exp(np.clip((D - fd0) / fsc, -50, 50)))
                        for D in Ds
                    ]
                )
                if pse >= 0.01:
                    probs.append(float(pse))
        sq10, smed, sq90 = rp_quantiles(max(len(seasons), 1), probs)
        sweep.append(
            {
                "t": thr,
                "scored": round((N_SEASONS + 1) / max(len(seasons), 1), 2),
                "med": round(smed, 2),
                "p10": round(sq10, 2),
                "p90": round(sq90, 2),
            }
        )
    t_rp3 = next((p["t"] for p in sweep if p["med"] >= 3.0), None)
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
            "gap_storms": gap_storms,
            "p_season": {str(k): v for k, v in p_season.items()},
            "expected_extra": round(float(sum(p_season.values())), 1),
            "rp_median": round(med, 1),
            "rp_p10": round(q10, 1),
            "rp_p90": round(q90, 1),
            "sweep": sweep,
            "t_rp3": t_rp3,
        },
        "calibration": {
            "logistic_midpoint_km": round(float(d0)),
            "logistic_scale_km": round(float(sc)),
        },
    }
    with open(DATA / "rp.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(json.dumps(out["combined"], indent=1))
    print("observational:", obs_seasons, "| action:", act_seasons)
    print(f"wrote {DATA/'rp.json'}")


if __name__ == "__main__":
    main()
