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
import numpy as np
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


# DB quadrant-array order, validated empirically: rebuilding Judy's 64 kt
# swath from storms.ibtracs_tracks_geo with this order reproduces the fji
# wind_buffers.parquet geometry at IoU 0.96 (vs 0.86 for ATCF ne,se,sw,nw)
DB_QUAD_ORDER = ("ne", "nw", "se", "sw")


def load_observed_wind_buffers(sids):
    """Observed wind swaths for ``sids``, one row per (sid, buffer_speed).

    Base source is the fji ``wind_buffers.parquet``; any (sid, speed) that
    is missing or has empty geometry there but has USA radii in
    ``storms.ibtracs_tracks_geo`` is rebuilt from the DB track (the parquet
    was built while some recent storms — Lola, Mal — were still provisional
    in IBTrACS, leaving their swaths empty or absent).
    Returns EPSG:3832 with valid geometries.
    """
    import io as _io

    from src.utils.wind_buffers import wind_buffers_from_track

    sids = set(sids)
    buf = gpd.read_parquet(
        _io.BytesIO(
            stratus.load_blob_data(
                "pa-aa-fji-storms/processed/ibtracs/wind_buffers.parquet"
            )
        )
    )
    buf = buf[buf["sid"].isin(sids)][["sid", "buffer_speed", "geometry"]]
    buf = buf.assign(geometry=buf.geometry.make_valid())

    with stratus.get_engine(stage="prod").connect() as con:
        tracks = pd.read_sql(
            "SELECT sid, valid_time, "
            " usa_quadrant_radius_34 q34, usa_quadrant_radius_50 q50, "
            " usa_quadrant_radius_64 q64, "
            " ST_X(geometry::geometry) lon, ST_Y(geometry::geometry) lat "
            "FROM storms.ibtracs_tracks_geo "
            f"WHERE sid IN ({','.join(repr(s) for s in sids)})",
            con,
        )
    tracks["lon"] = tracks["lon"] % 360

    def parse(v):
        try:
            out = [float(x) for x in str(v).strip("{}").split(",")]
            return out if len(out) == 4 else [np.nan] * 4
        except ValueError:
            return [np.nan] * 4

    ok = set(
        zip(
            buf.loc[~buf.geometry.is_empty, "sid"],
            buf.loc[~buf.geometry.is_empty, "buffer_speed"],
        )
    )
    rebuilt = []
    for sid, g in tracks.groupby("sid"):
        g = g.sort_values("valid_time")
        for speed in (34, 50, 64):
            if (sid, speed) in ok:
                continue
            arrs = g[f"q{speed}"].apply(parse)
            if not any(np.isfinite(a).any() for a in arrs):
                continue
            d = g[["valid_time", "lat", "lon"]].copy()
            for j, q in enumerate(DB_QUAD_ORDER):
                d[f"r{speed}_{q}"] = [a[j] for a in arrs]
            quad_cols = [f"r{speed}_{q}" for q in DB_QUAD_ORDER]
            # zero-fill BEFORE interpolation: otherwise radii bleed past
            # the last valid observation along the post-tropical tail
            d[quad_cols] = d[quad_cols].fillna(0)
            bufs = wind_buffers_from_track(d, speeds=(speed,))
            if bufs.empty or bufs.geometry.iloc[0].is_empty:
                continue
            rebuilt.append(
                {
                    "sid": sid,
                    "buffer_speed": speed,
                    "geometry": bufs.geometry.iloc[0],
                }
            )
    if rebuilt:
        print(
            "  rebuilt from DB radii: "
            + ", ".join(f"{r['sid']}@{r['buffer_speed']}" for r in rebuilt)
        )
        buf = buf[
            buf.apply(
                lambda r: (r["sid"], r["buffer_speed"])
                not in {(x["sid"], x["buffer_speed"]) for x in rebuilt},
                axis=1,
            )
        ]
        buf = pd.concat(
            [buf, gpd.GeoDataFrame(rebuilt, crs=3832)], ignore_index=True
        )
    return gpd.GeoDataFrame(buf, geometry="geometry", crs=3832)


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
    nat_union = adm2_exp.geometry.union_all()
    da_nat = da_wp.rio.clip([nat_union], all_touched=True)
    nat_pop = int(da_nat.where(da_nat > 0).sum())
    print(
        f"  AOI adm2: {len(aoi)}  AOI population: {aoi_pop:,}  "
        f"national: {nat_pop:,}"
    )

    def _exposure(geom_wrapped, union, da):
        """Population inside a buffer, within ``union``.

        ``geom_wrapped`` must be in the lon-wrapped frame (FJI_CRS,
        longitudes in [0, 360)) to match the raster grid — see _ring.
        """
        if geom_wrapped is None or geom_wrapped.is_empty:
            return 0
        if not geom_wrapped.is_valid:
            geom_wrapped = shapely.make_valid(geom_wrapped)
        if not geom_wrapped.intersects(union):
            return 0
        try:
            clipped = da.rio.clip([geom_wrapped])
        except Exception:
            return 0
        return int(clipped.where(clipped > 0).sum())

    def exposure(geom_wrapped):
        """Forecast exposure: AOI provinces only — the trigger's scope."""
        return _exposure(geom_wrapped, aoi_union, da_aoi)

    def exposure_national(geom_wrapped):
        """Observed exposure: the whole country — verification is against
        whether the storm actually hit Vanuatu, not just the AOI."""
        return _exposure(geom_wrapped, nat_union, da_nat)

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
    obs_buf = load_observed_wind_buffers(want_sids)

    # Recompute observed exposure from the observed buffers with the *same*
    # single-clip method used for the forecasts, rather than reading the
    # per-adm2 parquet on blob — otherwise the observed side would carry the
    # double-counting inflation described above. Scope differs deliberately:
    # forecasts are scored on the AOI (the trigger's domain) while observed
    # exposure counts the WHOLE country, so verification asks "did the storm
    # actually hit Vanuatu", not just the AOI provinces.
    print("recomputing observed exposure (single-clip, whole country)...")
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
                "pop_exposed": exposure_national(g.geometry.union_all()),
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

            # per-leg 64 kt exposure, from the framework's leadtime windows:
            # action = the 24-72 h track segment, readiness = 72-120 h
            leg = {}
            # NB: loop variable must not shadow the storm-level `name`
            for leg_key, lo, hi in (("A", 24, 72), ("R", 72, 120)):
                seg = g[(g["tau"] >= lo) & (g["tau"] <= hi)]
                leg[leg_key] = 0
                if seg.empty:
                    continue
                sb = wind_buffers_from_track(seg, speeds=(64,))
                if sb.empty:
                    continue
                gw = (
                    gpd.GeoSeries([sb.geometry.iloc[0]], crs=3832)
                    .to_crs(FJI_CRS)
                    .iloc[0]
                )
                leg[leg_key] = exposure(gw)

            key = init.strftime("%Y%m%d%H")
            cycles.append(
                {
                    "init": init.strftime("%Y-%m-%dT%H:%MZ"),
                    "exp": {str(s): int(exp.get(s, 0)) for s in SPEEDS},
                    "expA64": int(leg["A"]),
                    "expR64": int(leg["R"]),
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
                "nat_pop": nat_pop,
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
