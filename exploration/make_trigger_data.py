"""Generate the observed-record trigger data.

Writes:
    exploration/public/trigger_data.csv       — provenance / ad-hoc analysis
    docs/forecast-check/data/hist.json        — for the static JS page
    docs/forecast-check/data/obsgeom/<sid>.json — observed swaths + track,
        lazy-loaded by the trigger-design map

(The marimo explorer that consumed the CSV was retired 2026-08-11.)
"""
import json
from pathlib import Path

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
from make_forecast_check_data import (
    _ring,
    aoi_exposure_context,
    load_observed_veq_swaths,
    load_observed_wind_buffers,
)

from src.constants import ADM1_AOI_PCODES, FJI_CRS, PROJECT_PREFIX
from src.datasources import codab

adm2 = codab.load_codab_from_blob(admin_level=2)
aoi_pcodes = adm2[adm2["ADM1_PCODE"].isin(ADM1_AOI_PCODES)][
    "ADM2_PCODE"
].unique()

df_stats = stratus.load_parquet_from_blob(
    f"{PROJECT_PREFIX}/processed/impact_stats.parquet"
)

# _dedup: assigns each pixel to exactly one adm2 and keeps dateline-crossing
# swaths contiguous. The original adm2_usaradii_exp.parquet double counts
# boundary pixels (all_touched per-adm2 clips) and tears buffers that cross
# 180deg into a [-180, 180] smear that spuriously covers Vanuatu (Tomas 2010
# read 288k exposed at 64kt — more than the AOI's population). See
# exploration/recalc_adm2_exposure.py.
df_exp = stratus.load_parquet_from_blob(
    f"{PROJECT_PREFIX}/processed/ibtracs/adm2_usaradii_exp_dedup.parquet"
)
df_exp_aoi = df_exp[df_exp["ADM2_PCODE"].isin(aoi_pcodes)]

with stratus.get_engine(stage="prod").connect() as con:
    df_storms = pd.read_sql(
        "SELECT sid, name, season FROM storms.ibtracs_storms", con
    )

df_exp_aoi = df_exp_aoi.merge(df_storms, on="sid", how="left")
df_exp_aoi = df_exp_aoi[df_exp_aoi["season"] >= 2006]

df_exp_sid = (
    df_exp_aoi.groupby(["sid", "buffer_speed"])["pop_exposed"]
    .sum()
    .reset_index()
    .pivot(columns="buffer_speed", values="pop_exposed", index="sid")
    .reset_index()
)
df_exp_sid.columns.name = None
df_exp_sid = df_exp_sid.rename(columns={x: f"exp{x}" for x in [34, 50, 64]})
df_exp_sid = df_exp_sid.fillna(0)

df = df_stats.merge(df_exp_sid, on="sid", how="inner")
df = df[df["season"] >= 2006].reset_index(drop=True)

# The 64 kt trigger layer is read at the V_EQ contour (64 kt 10-min =
# ~73 kt 1-min): rebuild observed 64-kt exposure from DB radii at V_EQ.
# 34/50 kt columns stay 1-min context from the dedup parquet.
print("observed V_EQ swaths + AOI exposure for the 64 kt layer...")
veq_sw = load_observed_veq_swaths(set(df["sid"]))
expo_aoi = aoi_exposure_context()
veq_exp = {}
for sid, geom in veq_sw.items():
    gw = gpd.GeoSeries([geom], crs=3832).to_crs(FJI_CRS).iloc[0]
    veq_exp[sid] = expo_aoi(gw)
df["exp64"] = df["sid"].map(veq_exp).fillna(0).astype(int)

cols = [
    "sid",
    "name",
    "season",
    "exp34",
    "exp50",
    "exp64",
    "roll2_mean",
    "Total Affected",
    "cerf",
]
out_path = "exploration/public/trigger_data.csv"
df[cols].to_csv(out_path, index=False)
print(f"Saved {len(df)} rows to {out_path}")

# --- hist.json for the static page (docs/forecast-check/) ---
# The record is the 2006-2025 seasons (20). IBTrACS 64-kt radii only exist
# reliably from 2005 (2003-04 hurricane-strength points carry none — Ivy
# 2004, a direct AOI hit, is invisible); 2005 itself is excluded because
# its forecast archive is a single anomalous year (full decks survive for
# 2005 alone among 2003-2011, provenance unclear).
FIRST_SEASON, LAST_SEASON = 2006, 2025
# upper cap too: a storm from a season outside the record would silently
# corrupt the Weibull denominator (n_seasons)
dfj = df[df["season"].between(FIRST_SEASON, LAST_SEASON)].copy()
storms = [
    {
        "sid": r["sid"],
        "name": r["name"] if pd.notna(r["name"]) else "Unnamed",
        "season": int(r["season"]),
        "exp34": int(r["exp34"]),
        "exp50": int(r["exp50"]),
        "exp64": int(r["exp64"]),
        "rain": round(float(r["roll2_mean"]), 1),
        "affected": (
            int(r["Total Affected"]) if pd.notna(r["Total Affected"]) else 0
        ),
        "cerf": bool(r["cerf"]),
    }
    for r in dfj[cols].to_dict("records")
]
hist = {
    "first_season": FIRST_SEASON,
    "last_season": LAST_SEASON,
    "n_seasons": LAST_SEASON - FIRST_SEASON + 1,
    "target": 5,  # ~1-in-4 seasons over the 20-season record
    "storms": storms,
}
json_path = "docs/forecast-check/data/hist.json"
with open(json_path, "w") as f:
    json.dump(hist, f, separators=(",", ":"))
print(f"Saved {len(storms)} storms to {json_path}")

# --- observed swath + track geometry per storm, for the design-tab map ---
gdf_buf = load_observed_wind_buffers({s["sid"] for s in storms})

# tracks from the DB (vut_distances.parquet predates the 2022+ storms)
_sid_list = ",".join(repr(s["sid"]) for s in storms)
with stratus.get_engine(stage="prod").connect() as con:
    df_track = pd.read_sql(
        "SELECT sid, valid_time AS time, "
        " ST_Y(geometry::geometry) AS lat, ST_X(geometry::geometry) AS lon "
        f"FROM storms.ibtracs_tracks_geo WHERE sid IN ({_sid_list})",
        con,
    )

geom_dir = Path("docs/forecast-check/data/obsgeom")
geom_dir.mkdir(parents=True, exist_ok=True)
n_geo = 0
for s in storms:
    sid = s["sid"]
    rings = {}
    for speed in (34, 50):
        sel = gdf_buf[
            (gdf_buf["sid"] == sid) & (gdf_buf["buffer_speed"] == speed)
        ]
        rings[str(speed)] = (
            _ring(sel.geometry.union_all()) if len(sel) else None
        )
    # 64 kt ring at the V_EQ contour, matching the exposure numbers
    rings["64"] = _ring(veq_sw[sid]) if sid in veq_sw else None
    tr = df_track[df_track["sid"] == sid].sort_values("time")
    tr = tr.iloc[:: max(1, len(tr) // 300)]
    track = [[round(r.lat, 2), round(r.lon % 360, 2)] for r in tr.itertuples()]
    with open(geom_dir / f"{sid}.json", "w") as f:
        json.dump({"rings": rings, "track": track}, f, separators=(",", ":"))
    n_geo += 1
print(f"Saved {n_geo} storm geometries to {geom_dir}")
