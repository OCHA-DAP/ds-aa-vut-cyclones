"""Build the data for the forecast trigger-check page (docs/forecast-check/).

For every JTWC forecast cycle in the VMGD historical archive, rebuild the
34/50/64 kt wind swath from the forecast quadrant radii and compute the
population exposed inside the three AOI provinces, using the *same* method as
the observed IBTrACS exposure already used for this framework. VMGD's own
forecast tracks are carried through for context only — they have no wind
radii, so they cannot produce an exposure number.

Writes:
    docs/forecast-check/data/core.json          — storms, cycles, exposures
    docs/forecast-check/data/geom/<storm>.json  — buffers + tracks, lazy loaded

Run from the repo root:
    uv run python exploration/make_forecast_check_data.py
"""

import json
import os
import re
from pathlib import Path

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
import shapely
from shapely.geometry import box

from src.constants import ADM1_AOI_PCODES, CERF_SIDS, FJI_CRS, PROJECT_PREFIX
from src.datasources import codab, vmgd
from src.utils.wind_buffers import wind_buffers_from_track

ZIP = os.path.expanduser("~/Downloads/OneDrive_2026-08-05.zip")
OUT = Path("docs/forecast-check/data")
SPEEDS = (34, 50, 64)
# geometry simplification for the browser (metres, in EPSG:3832)
SIMPLIFY_M = 2000


def build_adm2_expanded(adm2: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """adm2 expanded 500 m into the sea, then made non-overlapping.

    Replicates exploration/hist_exp.md, which is how the observed exposure
    numbers on blob were produced.
    """
    adm2_proj = adm2.to_crs(3832)
    land = adm2_proj.dissolve()
    minx, miny, maxx, maxy = land.total_bounds
    pad = 50000
    sea_box = gpd.GeoDataFrame(
        geometry=[box(minx - pad, miny - pad, maxx + pad, maxy + pad)],
        crs=adm2_proj.crs,
    )
    ocean = gpd.overlay(sea_box, land, how="difference")

    buffered = adm2_proj.copy()
    buffered["geometry"] = adm2_proj.buffer(500)
    buffer_only = gpd.overlay(buffered, adm2_proj, how="difference")
    buffer_ocean = gpd.overlay(buffer_only, ocean, how="intersection")[
        ["ADM2_PCODE", "geometry"]
    ]

    combined = gpd.GeoDataFrame(
        pd.concat([adm2_proj, buffer_ocean], ignore_index=True),
        crs=adm2_proj.crs,
    )
    expanded = combined.dissolve(by="ADM2_PCODE").reset_index().to_crs(4326)
    expanded = expanded.sort_values("ADM2_PCODE").reset_index(drop=True)

    clean, used = [], None
    for geom in expanded.geometry:
        if used is None:
            clean.append(geom)
            used = geom
        else:
            g = geom.difference(used)
            clean.append(g)
            used = used.union(g)
    expanded["geometry"] = clean
    return expanded


def _ring(geom, simplify_m=SIMPLIFY_M):
    """Simplified lon-wrapped coords for the browser, rounded to 3 dp.

    FJI_CRS keeps longitude in [0, 360), so swaths crossing the dateline
    stay contiguous — a plain to_crs(4326) tears them into a [-180, 180]
    smear. Leaflet renders longitudes past 180 fine.
    """
    if geom is None or geom.is_empty:
        return None
    g = (
        gpd.GeoSeries([geom], crs=3832)
        .simplify(simplify_m)
        .to_crs(FJI_CRS)
        .iloc[0]
    )
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
    out = []
    for p in polys:
        coords = [[round(x, 3), round(y, 3)] for x, y in p.exterior.coords]
        if len(coords) > 3:
            out.append(coords)
    return out or None


def main():
    print("loading boundaries + population...")
    adm2 = codab.load_codab_from_blob(admin_level=2)
    aoi_pcodes = set(
        adm2[adm2["ADM1_PCODE"].isin(ADM1_AOI_PCODES)]["ADM2_PCODE"]
    )
    adm2_exp = build_adm2_expanded(adm2)
    aoi = adm2_exp[adm2_exp["ADM2_PCODE"].isin(aoi_pcodes)].reset_index(
        drop=True
    )

    da_wp = stratus.open_blob_cog(
        "worldpop/pop_count/global_pop_2026_CN_1km_R2025A_UA_v1.tif",
        container_name="raster",
    )
    da_wp = da_wp.rio.clip(adm2_exp.geometry).squeeze(drop=True).compute()
    da_wp = da_wp.assign_coords({"x": ((da_wp.x + 360) % 360)}).sortby("x")

    # Clip the population raster to the AOI *once*, on the dissolved geometry.
    #
    # NB: the observed pipeline (exploration/hist_exp.md) clips each adm2 with
    # all_touched=True and then sums across adm2. On 1 km pixels and narrow
    # islands that double counts boundary pixels — it puts the AOI total at
    # 302,282 against a true population of 245,332 (+23%), so exposure can
    # exceed the population that actually lives there. Dissolving first keeps
    # every coastal pixel (all_touched=True on the union) without counting any
    # pixel twice.
    aoi_union = aoi.geometry.union_all()
    da_aoi = da_wp.rio.clip([aoi_union], all_touched=True)
    aoi_pop = int(da_aoi.where(da_aoi > 0).sum())
    print(f"  AOI adm2: {len(aoi)}  AOI population: {aoi_pop:,}")

    def exposure(geom_wrapped):
        """Population inside a buffer, within the AOI provinces only.

        ``geom_wrapped`` must be in the lon-wrapped frame (FJI_CRS,
        longitudes in [0, 360)) to match the raster grid — see _ring.
        """
        if geom_wrapped is None or geom_wrapped.is_empty:
            return 0
        if not geom_wrapped.is_valid:
            geom_wrapped = shapely.make_valid(geom_wrapped)
        if not geom_wrapped.intersects(aoi_union):
            return 0
        try:
            clipped = da_aoi.rio.clip([geom_wrapped])
        except Exception:
            return 0
        return int(clipped.where(clipped > 0).sum())

    print("parsing ATCF decks from the archive...")
    atcf = vmgd.parse_atcf_from_archive(ZIP)
    jtwc = vmgd.wide_radii(atcf[atcf["tech"] == "JTWC"])
    # drop invest-numbered decks (90-99): each duplicates a named storm
    jtwc = jtwc[jtwc["storm"].str[2:4].astype(int) < 90]
    print(
        f"  storms: {jtwc.storm.nunique()}  cycles: "
        f"{jtwc.groupby(['storm', 'init']).ngroups}"
    )

    print("parsing VMGD forecast track maps...")
    vm = vmgd.parse_vmgd_forecasts_from_archive(ZIP)

    with stratus.get_engine(stage="prod").connect() as con:
        ib = pd.read_sql(
            "SELECT sid, atcf_id, name, season FROM storms.ibtracs_storms",
            con,
        )
    ib["atcf_u"] = ib["atcf_id"].str.upper()
    id2sid = dict(zip(ib["atcf_u"], ib["sid"]))
    id2name = dict(zip(ib["atcf_u"], ib["name"]))
    want_sids = {id2sid[s] for s in jtwc["storm"].unique() if s in id2sid}

    print("loading observed buffers...")
    import io as _io

    obs_buf = gpd.read_parquet(
        _io.BytesIO(
            stratus.load_blob_data(
                "pa-aa-fji-storms/processed/ibtracs/wind_buffers.parquet"
            )
        )
    )

    # Recompute observed exposure from the observed buffers with the *same*
    # single-clip method used for the forecasts, rather than reading the
    # per-adm2 parquet on blob — otherwise the observed side would carry the
    # double-counting inflation described above and the two columns on the
    # page would not be comparable.
    print("recomputing observed exposure (single-clip, AOI only)...")
    # make_valid in the metric CRS first (a few swaths self-intersect),
    # then convert to the lon-wrapped frame so dateline-crossing swaths
    # (Winston, Yasa, ...) stay contiguous instead of tearing into a
    # [-180, 180] smear that spuriously covers Vanuatu
    obs_buf_aoi = obs_buf[obs_buf["sid"].isin(want_sids)]
    obs_buf_aoi = obs_buf_aoi.assign(
        geometry=obs_buf_aoi.geometry.make_valid()
    ).to_crs(FJI_CRS)
    obs_rows = []
    for (sid, speed), g in obs_buf_aoi.groupby(["sid", "buffer_speed"]):
        obs_rows.append(
            {
                "sid": sid,
                "buffer_speed": int(speed),
                "pop_exposed": exposure(g.geometry.union_all()),
            }
        )
    obs_tot = (
        pd.DataFrame(obs_rows)
        .pivot(index="sid", columns="buffer_speed", values="pop_exposed")
        .fillna(0)
        .astype(int)
    )

    # observed track points, for drawing the observed track on the map
    obs_track = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/vut_distances.parquet"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "geom").mkdir(exist_ok=True)

    storms_out = []
    for storm, sdf in jtwc.groupby("storm"):
        sid = id2sid.get(storm)
        season = int(storm[-4:])
        name = id2name.get(storm) or ""
        if not isinstance(name, str) or not name:
            nm = atcf[(atcf["storm"] == storm) & (atcf["name"].str.len() > 2)]
            name = nm["name"].mode().iloc[0] if len(nm) else storm
        name = str(name).title()
        # unnamed systems come through as "Invest"/"92P" — use the deck id
        if name.lower() == "invest" or re.fullmatch(r"\d+[a-z]", name, re.I):
            name = storm

        cycles, geoms = [], {}
        for init, g in sdf.groupby("init"):
            g = g.sort_values("tau")
            bufs = wind_buffers_from_track(g)
            exp = {}
            ring64 = None
            for _, b in bufs.iterrows():
                sp = int(b["buffer_speed"])
                geom_w = (
                    gpd.GeoSeries([b.geometry], crs=3832)
                    .to_crs(FJI_CRS)
                    .iloc[0]
                )
                exp[sp] = exposure(geom_w)
                if sp == 64:
                    ring64 = _ring(b.geometry)
            if not exp:
                continue
            key = init.strftime("%Y%m%d%H")
            cycles.append(
                {
                    "init": init.strftime("%Y-%m-%dT%H:%MZ"),
                    "exp": {str(s): int(exp.get(s, 0)) for s in SPEEDS},
                    "vmax": int(g["vmax"].max()),
                }
            )
            geoms[key] = {
                "buf64": ring64,
                "track": [
                    [round(r.lat, 2), round(r.lon, 2), int(r.tau)]
                    for r in g.itertuples()
                ],
            }

        if not cycles:
            continue

        obs = {}
        if sid is not None and sid in obs_tot.index:
            obs = {str(s): int(obs_tot.loc[sid].get(s, 0)) for s in SPEEDS}

        # VMGD forecast tracks that overlap this storm's forecast window
        t0 = sdf["init"].min() - pd.Timedelta(days=3)
        t1 = sdf["init"].max() + pd.Timedelta(days=3)
        vmgd_tracks = []
        if not vm.empty:
            sel = vm[(vm["issue_time"] >= t0) & (vm["issue_time"] <= t1)]
            # keep only maps whose positions are near this storm's track
            for (src, num), g2 in sel.groupby(["source", "number"]):
                g2 = g2.sort_values("tau")
                lat0, lon0 = g2.iloc[0]["lat"], g2.iloc[0]["lon"]
                near = sdf[
                    (sdf["init"] - g2.iloc[0]["issue_time"]).abs()
                    < pd.Timedelta(hours=12)
                ]
                if near.empty:
                    continue
                if min(abs(near["lat"] - lat0) + abs(near["lon"] - lon0)) > 6:
                    continue
                vmgd_tracks.append(
                    {
                        "issue": g2.iloc[0]["issue_time"].strftime(
                            "%Y-%m-%dT%H:%MZ"
                        ),
                        "number": int(num) if pd.notna(num) else None,
                        "track": [
                            [
                                round(r.lat, 2),
                                round(r.lon, 2),
                                int(r.tau),
                                str(r.category),
                            ]
                            for r in g2.itertuples()
                        ],
                    }
                )
        vmgd_tracks.sort(key=lambda d: d["issue"])

        # observed 64 kt buffer + observed track
        obs_ring, obs_pts = None, []
        if sid is not None:
            ob = obs_buf[
                (obs_buf["sid"] == sid) & (obs_buf["buffer_speed"] == 64)
            ]
            if len(ob):
                obs_ring = _ring(ob.iloc[0].geometry)
            ot = obs_track[obs_track["sid"] == sid].sort_values("time")
            ot = ot.iloc[:: max(1, len(ot) // 300)]
            obs_pts = [
                [round(r.lat, 2), round(r.lon % 360, 2)]
                for r in ot.itertuples()
            ]

        with open(OUT / "geom" / f"{storm}.json", "w") as f:
            json.dump(
                {
                    "cycles": geoms,
                    "obs_buf64": obs_ring,
                    "obs_track": obs_pts,
                    "vmgd": vmgd_tracks,
                },
                f,
                separators=(",", ":"),
            )

        storms_out.append(
            {
                "id": storm,
                "sid": sid,
                "name": name,
                "season": season,
                "obs": obs,
                "cerf": sid in CERF_SIDS,
                "cycles": cycles,
                "n_vmgd": len(vmgd_tracks),
            }
        )
        print(
            f"  {storm} {name:12s} cycles={len(cycles):3d} "
            f"peak64_fcst={max(c['exp']['64'] for c in cycles):7d} "
            f"obs64={obs.get('64', 0):7d} vmgd_maps={len(vmgd_tracks)}"
        )

    storms_out.sort(key=lambda s: (s["season"], s["id"]))
    aoi_geo = (
        aoi.dissolve()
        .simplify(0.01)
        .to_crs(4326)
        .__geo_interface__["features"][0]["geometry"]
    )
    with open(OUT / "core.json", "w") as f:
        json.dump(
            {
                "generated": pd.Timestamp.utcnow().strftime(
                    "%Y-%m-%d %H:%M UTC"
                ),
                "aoi_provinces": ADM1_AOI_PCODES,
                "aoi_pop": aoi_pop,
                "aoi_geom": aoi_geo,
                "speeds": list(SPEEDS),
                "storms": storms_out,
            },
            f,
            separators=(",", ":"),
        )
    print(f"\nwrote {OUT/'core.json'} — {len(storms_out)} storms")


if __name__ == "__main__":
    main()
