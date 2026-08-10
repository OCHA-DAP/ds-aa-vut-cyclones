"""Recompute per-adm2 wind exposure without boundary double-counting.

The original ``processed/ibtracs/adm2_usaradii_exp.parquet`` (built by
``exploration/hist_exp.md``) clips the population raster per adm2 with
``all_touched=True``. A 1 km pixel touching two adm2 polygons is counted
fully in both rows, so any total summed across adm2 is inflated — the AOI
(Shefa/Sanma/Tafea) sums to 302,282 against a true population of 245,332
(+23%).

Two corrected assignment methods, selected with ``--method``:

``priority`` (default)
    Whole-pixel: every pixel goes to exactly one adm2. Admins are visited in
    sorted ADM2_PCODE order over the seaward-extended polygons (see
    ``build_adm2_expanded``) and each takes only the pixels not already
    claimed. Simple and partition-exact, but pixels contested between two
    admins' ocean extensions (straits, lagoons) are settled by sort order —
    e.g. Port Vila's ring claims Ifira islet's near-shore pixels.

``fairsplit``
    Fractional: each pixel's population is split across adm2 in proportion
    to each admin's *land* area within the pixel (measured on a 10x
    supersampled grid of the original, unextended polygons — land membership
    is unambiguous, so no de-overlap heuristic is needed at all). Populated
    pixels containing no mapped land (offshore islets missing from CODAB)
    fall back to the nearest admin. Equivalent to dividing the raster by the
    dissolved-land coverage fraction and running an exactextract sum.
    Shares sum to 1 per pixel, so totals partition exactly at every level.

Writes ``processed/ibtracs/adm2_usaradii_exp_{dedup|fairsplit}.parquet`` to
blob (dev). The original parquet is left untouched.

Run from the repo root:
    uv run python exploration/recalc_adm2_exposure.py [--method fairsplit]
"""

import argparse
import io

import geopandas as gpd
import numpy as np
import ocha_stratus as stratus
import pandas as pd
from make_forecast_check_data import (
    build_adm2_expanded,
    load_observed_wind_buffers,
)
from rasterio import Affine
from rasterio.features import geometry_mask
from shapely.geometry import Point

from src.constants import ADM1_AOI_PCODES, FJI_CRS, PROJECT_PREFIX
from src.datasources import codab

SUPERSAMPLE = 10  # subpixels per pixel side for the fairsplit method


def shares_priority(adm2, shape, transform):
    """One-hot pixel->adm2 shares: first-come priority over the extended
    polygons, all_touched=True (the ``_dedup`` method)."""
    adm2_exp = build_adm2_expanded(adm2).sort_values("ADM2_PCODE")
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
    shares = np.zeros((len(pcodes),) + shape, dtype=np.float32)
    for i in range(len(pcodes)):
        shares[i][assign == i] = 1.0
    return pcodes, shares


def shares_fairsplit(adm2, pop, shape, transform):
    """Fractional pixel->adm2 shares: proportional to each admin's land
    area within the pixel, on a supersampled grid of the ORIGINAL
    polygons. Nearest-admin fallback for populated pixels with no land."""
    adm2 = adm2.sort_values("ADM2_PCODE").reset_index(drop=True)
    pcodes = adm2["ADM2_PCODE"].tolist()
    s = SUPERSAMPLE
    sub_shape = (shape[0] * s, shape[1] * s)
    t = transform
    sub_t = Affine(t.a / s, t.b, t.c, t.d, t.e / s, t.f)

    counts = np.zeros((len(pcodes),) + shape, dtype=np.uint8)
    claimed = np.zeros(sub_shape, dtype=bool)
    for i, geom in enumerate(adm2.geometry):
        m = ~geometry_mask([geom], out_shape=sub_shape, transform=sub_t)
        m &= ~claimed  # CODAB slivers: first-come on the (tiny) overlaps
        claimed |= m
        counts[i] = (
            m.reshape(shape[0], s, shape[1], s).sum(axis=(1, 3))
        ).astype(np.uint8)

    land_total = counts.sum(axis=0, dtype=np.int32)
    shares = np.zeros((len(pcodes),) + shape, dtype=np.float32)
    has_land = land_total > 0
    for i in range(len(pcodes)):
        shares[i][has_land] = counts[i][has_land] / land_total[has_land]

    # populated pixels with no mapped land -> nearest admin, whole pixel
    orphan = (pop > 0) & ~has_land
    if orphan.any():
        proj = adm2.to_crs(3832)
        rows, cols = np.where(orphan)
        pts = gpd.GeoSeries(
            [Point(*(t * (c + 0.5, r + 0.5))) for r, c in zip(rows, cols)],
            crs="EPSG:4326",
        ).to_crs(3832)
        for (r, c), pt in zip(zip(rows, cols), pts):
            d = proj.geometry.distance(pt)
            shares[int(np.argmin(d.values)), r, c] = 1.0
        print(
            f"  orphan populated pixels (no mapped land): {orphan.sum()}, "
            f"pop {pop[orphan].sum():,.0f} -> nearest admin"
        )
    return pcodes, shares


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--method", choices=["priority", "fairsplit"], default="priority"
    )
    args = ap.parse_args()
    suffix = {"priority": "dedup", "fairsplit": "fairsplit"}[args.method]
    out_blob = (
        f"{PROJECT_PREFIX}/processed/ibtracs/"
        f"adm2_usaradii_exp_{suffix}.parquet"
    )

    print("loading boundaries + population...")
    adm2 = codab.load_codab_from_blob(admin_level=2)
    adm2_exp = build_adm2_expanded(adm2)

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
    print(f"  grid {shape}, pop on grid {pop.sum():,.0f}")

    print(f"building pixel->adm2 shares ({args.method})...")
    if args.method == "priority":
        pcodes, shares = shares_priority(adm2, shape, transform)
    else:
        pcodes, shares = shares_fairsplit(adm2, pop, shape, transform)

    per_adm2_pop = (shares * pop).sum(axis=(1, 2))
    print(
        f"  allocated pop {per_adm2_pop.sum():,.0f} "
        f"(grid total {pop.sum():,.0f})"
    )
    aoi_pcodes = set(
        adm2[adm2["ADM1_PCODE"].isin(ADM1_AOI_PCODES)]["ADM2_PCODE"]
    )
    aoi_idx = [i for i, p in enumerate(pcodes) if p in aoi_pcodes]
    print(f"  AOI pop: {per_adm2_pop[aoi_idx].sum():,.0f}")

    # --- observed wind buffers ---
    # the fji parquet augmented with DB-rebuilt swaths for storms it is
    # missing or empty for (Lola, Mal — provisional in IBTrACS at its build
    # time); scope = every sid in the parquet plus recent SP-basin storms
    print("loading observed wind buffers...")
    base = gpd.read_parquet(
        io.BytesIO(
            stratus.load_blob_data(
                "pa-aa-fji-storms/processed/ibtracs/wind_buffers.parquet"
            )
        )
    )
    with stratus.get_engine(stage="prod").connect() as con:
        sp = pd.read_sql(
            "SELECT sid FROM storms.ibtracs_storms "
            "WHERE genesis_basin='SP' AND season >= 2001",
            con,
        )
    gdf_buf = load_observed_wind_buffers(set(base["sid"]) | set(sp["sid"]))
    # FJI_CRS wraps longitude to [0, 360), so buffers crossing the dateline
    # (Winston, Gretel, ...) stay contiguous and line up with the raster grid
    gdf_buf = gdf_buf.to_crs(FJI_CRS)
    print(
        f"  {gdf_buf.sid.nunique()} storms x "
        f"{sorted(gdf_buf.buffer_speed.unique())} kt"
    )

    print("computing exposure...")
    pop_shares = shares * pop  # (n_adm2, H, W), people per admin per pixel
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
        exposed = pop_shares[:, in_buf].sum(axis=1)
        for i, pcode in enumerate(pcodes):
            records.append(
                {
                    "sid": sid,
                    "buffer_speed": int(speed),
                    "ADM2_PCODE": pcode,
                    "pop_exposed": int(round(exposed[i])),
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

    stratus.upload_parquet_to_blob(df, out_blob)
    print(f"uploaded {out_blob}")


if __name__ == "__main__":
    main()
