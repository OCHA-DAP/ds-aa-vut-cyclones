"""Recompute per-adm2 wind exposure without boundary double-counting.

The existing ``processed/ibtracs/adm2_usaradii_exp.parquet`` (built by
``exploration/hist_exp.md``) clips the population raster per adm2 with
``all_touched=True``. A 1 km pixel touching two adm2 polygons is counted
fully in both rows, so any total summed across adm2 is inflated — the AOI
(Shefa/Sanma/Tafea) sums to 302,282 against a true population of 245,332
(+23%).

This script assigns every pixel to exactly one adm2 instead: adm2 are
visited in sorted ADM2_PCODE order (the same sequential priority
``hist_exp.md`` uses to de-overlap the vector geometries) and each takes
only the pixels not already claimed. Per-adm2 rows then sum exactly to the
dissolved-clip total, at any aggregation level.

Writes ``processed/ibtracs/adm2_usaradii_exp_dedup.parquet`` to blob (dev).
The original parquet is left untouched.

Run from the repo root:
    uv run python exploration/recalc_adm2_exposure.py
"""

import io

import geopandas as gpd
import numpy as np
import ocha_stratus as stratus
import pandas as pd
from make_forecast_check_data import build_adm2_expanded
from rasterio.features import geometry_mask

from src.constants import ADM1_AOI_PCODES, FJI_CRS, PROJECT_PREFIX
from src.datasources import codab

OUT_BLOB = (
    f"{PROJECT_PREFIX}/processed/ibtracs/adm2_usaradii_exp_dedup.parquet"
)


def main():
    print("loading boundaries + population...")
    adm2 = codab.load_codab_from_blob(admin_level=2)
    adm2_exp = build_adm2_expanded(adm2).sort_values("ADM2_PCODE")

    da = stratus.open_blob_cog(
        "worldpop/pop_count/global_pop_2026_CN_1km_R2025A_UA_v1.tif",
        container_name="raster",
    )
    da = da.rio.clip(
        [adm2_exp.geometry.union_all()], all_touched=True
    ).squeeze(drop=True)
    da = da.compute()
    da = da.assign_coords({"x": ((da.x + 360) % 360)}).sortby("x")

    pop = da.values.astype("float64")
    pop[~np.isfinite(pop) | (pop < 0)] = 0.0
    transform = da.rio.transform()
    shape = pop.shape
    total_pop = pop.sum()
    print(f"  grid {shape}, national pop {total_pop:,.0f}")

    # --- pixel -> adm2 assignment, sequential priority, all_touched=True ---
    pcodes = adm2_exp["ADM2_PCODE"].tolist()
    assign = np.full(shape, -1, dtype=np.int32)
    for i, (_, row) in enumerate(adm2_exp.iterrows()):
        touched = ~geometry_mask(
            [row.geometry],
            out_shape=shape,
            transform=transform,
            all_touched=True,
        )
        assign[(assign == -1) & touched] = i

    per_adm2_pop = np.bincount(
        assign[assign >= 0], weights=pop[assign >= 0], minlength=len(pcodes)
    )
    print(
        f"  assigned pixels sum {per_adm2_pop.sum():,.0f} "
        f"(dissolved-clip total {total_pop:,.0f})"
    )
    aoi_pcodes = set(
        adm2[adm2["ADM1_PCODE"].isin(ADM1_AOI_PCODES)]["ADM2_PCODE"]
    )
    aoi_idx = [i for i, p in enumerate(pcodes) if p in aoi_pcodes]
    print(f"  AOI pop (assigned): {per_adm2_pop[aoi_idx].sum():,.0f}")

    # --- observed wind buffers ---
    print("loading observed wind buffers...")
    gdf_buf = gpd.read_parquet(
        io.BytesIO(
            stratus.load_blob_data(
                "pa-aa-fji-storms/processed/ibtracs/wind_buffers.parquet"
            )
        )
    )
    # FJI_CRS wraps longitude to [0, 360), so buffers crossing the dateline
    # (Winston, Gretel, ...) stay contiguous and line up with the raster grid
    gdf_buf = gdf_buf.assign(geometry=gdf_buf.geometry.make_valid()).to_crs(
        FJI_CRS
    )
    print(
        f"  {gdf_buf.sid.nunique()} storms x "
        f"{sorted(gdf_buf.buffer_speed.unique())} kt"
    )

    print("computing exposure...")
    records = []
    for (sid, speed), g in gdf_buf.groupby(["sid", "buffer_speed"]):
        geom = g.geometry.union_all()
        if geom.is_empty:
            in_buf = np.zeros(shape, dtype=bool)
        else:
            # buffer mask uses the default all_touched=False, matching the
            # rio.clip(...) call in the original exposure calculation
            in_buf = ~geometry_mask(
                [geom], out_shape=shape, transform=transform
            )
        sel = in_buf & (assign >= 0)
        counts = np.bincount(
            assign[sel], weights=pop[sel], minlength=len(pcodes)
        )
        for i, pcode in enumerate(pcodes):
            records.append(
                {
                    "sid": sid,
                    "buffer_speed": int(speed),
                    "ADM2_PCODE": pcode,
                    "pop_exposed": int(counts[i]),
                }
            )
    df = pd.DataFrame(records)
    print(f"  {len(df):,} rows")

    # sanity: AOI sums must never exceed AOI population
    aoi_sum = (
        df[df["ADM2_PCODE"].isin(aoi_pcodes)]
        .groupby(["sid", "buffer_speed"])["pop_exposed"]
        .sum()
    )
    print(
        f"  max AOI exposure {aoi_sum.max():,} "
        f"(<= {per_adm2_pop[aoi_idx].sum():,.0f})"
    )

    stratus.upload_parquet_to_blob(df, OUT_BLOB)
    print(f"uploaded {OUT_BLOB}")


if __name__ == "__main__":
    main()
