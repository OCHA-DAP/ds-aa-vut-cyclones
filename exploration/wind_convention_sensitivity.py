"""Sensitivity: action-leg backtest under a 10-min reading of '64 kt'.

64 kt (10-min) = 64/0.93 = 69 kt (1-min, Harper 2010) or 64/0.88 = 73 kt
(1-min, classic WMO factor). Radii at those thresholds are estimated per
quadrant by log-linear extrapolation of the R50->R64 decay, gated on the
cycle's forecast vmax.
"""

import os
import sys

import geopandas as gpd
import numpy as np
import shapely

sys.path.insert(0, "exploration")
import ocha_stratus as stratus  # noqa: E402
from make_forecast_check_data import build_adm2_expanded  # noqa: E402

from src.constants import ADM1_AOI_PCODES, FJI_CRS  # noqa: E402
from src.datasources import codab, vmgd  # noqa: E402
from src.utils.wind_buffers import wind_buffers_from_track  # noqa: E402

ZIP = os.path.expanduser("~/Downloads/OneDrive_2026-08-05.zip")
KERRY_DECK = (
    "/private/tmp/claude-501/-Users-tdowning-OCHA-repos-ds-aa-vut-cyclones/"
    "e13a8b42-3af5-4d7c-aca7-d0e321f1a3d9/scratchpad/gap_adecks/2005/"
    "ash082005.dat"
)
STORMS = {  # atcf storm id -> label
    "SH102012": "Jasmine 2012",
    "SH172015": "Pam 2015",
    "SH182017": "Donna 2017",
    "SH252020": "Harold 2020",
    "SH152023": "Judy 2023",
    "SH162023": "Kevin 2023",
    "SH012024": "Lola 2024",
    "SH082005": "Kerry 2005",
}
QUADS = ("ne", "se", "sw", "nw")

# ---- AOI exposure setup (as in the page export) ----
adm2 = codab.load_codab_from_blob(admin_level=2)
aoi_pcodes = set(adm2[adm2.ADM1_PCODE.isin(ADM1_AOI_PCODES)].ADM2_PCODE)
adm2_exp = build_adm2_expanded(adm2)
aoi = adm2_exp[adm2_exp.ADM2_PCODE.isin(aoi_pcodes)]
da = stratus.open_blob_cog(
    "worldpop/pop_count/global_pop_2026_CN_1km_R2025A_UA_v1.tif",
    container_name="raster",
)
da = da.rio.clip(adm2_exp.geometry).squeeze(drop=True).compute()
da = da.assign_coords({"x": ((da.x + 360) % 360)}).sortby("x")
aoi_union = aoi.geometry.union_all()
da_aoi = da.rio.clip([aoi_union], all_touched=True)


def exposure(gw):
    if gw is None or gw.is_empty:
        return 0
    if not gw.is_valid:
        gw = shapely.make_valid(gw)
    if not gw.intersects(aoi_union):
        return 0
    try:
        c = da_aoi.rio.clip([gw])
    except Exception:
        return 0
    return int(c.where(c > 0).sum())


# ---- forecasts ----
atcf = vmgd.parse_atcf_from_archive(ZIP)
kerry = vmgd.parse_atcf_deck(
    open(KERRY_DECK, errors="replace").read(), "SH082005", "a"
)
import pandas as pd  # noqa: E402

kdf = pd.DataFrame(kerry)
kdf["init"] = pd.to_datetime(kdf["init"], format="%Y%m%d%H", utc=True)
kdf["valid_time"] = kdf["init"] + pd.to_timedelta(kdf["tau"], unit="h")
kdf = kdf.drop_duplicates(
    subset=["storm", "tech", "init", "tau", "rad"], keep="last"
)
atcf = pd.concat([atcf, kdf], ignore_index=True)
jt = vmgd.wide_radii(atcf[atcf.tech == "JTWC"])
jt = jt[jt.storm.isin(STORMS)]


def r_equiv(row, v_eq):
    """Per-quadrant radius of the v_eq (1-min) contour, from R50/R64."""
    out = {}
    for q in QUADS:
        r64 = row.get(f"r64_{q}") or 0
        r50 = row.get(f"r50_{q}") or 0
        if not np.isfinite(r64):
            r64 = 0
        if not np.isfinite(r50):
            r50 = 0
        if r64 <= 0 or row["vmax"] < v_eq:
            out[q] = 0.0
        elif r50 > r64:
            # log-linear decay of radius with wind speed
            lr = np.log(r64) + (v_eq - 64) * (
                (np.log(r64) - np.log(r50)) / (64 - 50)
            )
            out[q] = float(np.exp(lr))
        else:
            out[q] = r64 * (0.92 if v_eq < 70 else 0.85)
    return out


print(
    f"{'storm':14}{'1-min 64kt':>11}{'69kt eq (0.93)':>15}"
    f"{'73kt eq (0.88)':>15}"
)
for sid, label in STORMS.items():
    g = jt[jt.storm == sid]
    peaks = {"base": 0, "v69": 0, "v73": 0}
    for init, cyc in g.groupby("init"):
        seg = cyc[(cyc.tau >= 24) & (cyc.tau <= 72)].sort_values("tau")
        if seg.empty:
            continue
        for key, v_eq in (("base", 64), ("v69", 69), ("v73", 73)):
            d = seg[["valid_time", "lat", "lon"]].copy()
            if key == "base":
                for q in QUADS:
                    d[f"r64_{q}"] = seg[f"r64_{q}"].values
            else:
                eq = [r_equiv(r, v_eq) for _, r in seg.iterrows()]
                for q in QUADS:
                    d[f"r64_{q}"] = [e[q] for e in eq]
            d[[f"r64_{q}" for q in QUADS]] = d[
                [f"r64_{q}" for q in QUADS]
            ].fillna(0)
            if (d[[f"r64_{q}" for q in QUADS]].to_numpy() <= 0).all():
                continue
            bufs = wind_buffers_from_track(d, speeds=(64,))
            if bufs.empty:
                continue
            gw = (
                gpd.GeoSeries([bufs.geometry.iloc[0]], crs=3832)
                .to_crs(FJI_CRS)
                .iloc[0]
            )
            peaks[key] = max(peaks[key], exposure(gw))
    print(
        f"{label:14}{peaks['base']:>11,}{peaks['v69']:>15,}"
        f"{peaks['v73']:>15,}"
    )
