"""Generate the observed-record trigger data.

Writes:
    exploration/public/trigger_data.csv     — for the marimo WASM export
    docs/forecast-check/data/hist.json      — for the static JS page
"""
import json

import ocha_stratus as stratus
import pandas as pd

from src.constants import ADM1_AOI_PCODES, PROJECT_PREFIX
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
df_exp_aoi = df_exp_aoi[df_exp_aoi["season"] >= 2001]

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
df = df[df["season"] >= 2001].reset_index(drop=True)

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
# The record used for return periods is the 2003-2025 seasons (23 seasons);
# see the trigger explorer notes.
FIRST_SEASON, LAST_SEASON = 2003, 2025
dfj = df[df["season"] >= FIRST_SEASON].copy()
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
    "target": 6,
    "storms": storms,
}
json_path = "docs/forecast-check/data/hist.json"
with open(json_path, "w") as f:
    json.dump(hist, f, separators=(",", ":"))
print(f"Saved {len(storms)} storms to {json_path}")
