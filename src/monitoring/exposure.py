"""Population exposed to a wind swath, counted the way the trigger was
designed.

The 15,000-people threshold was calibrated in
``exploration/make_forecast_check_data.py`` (``aoi_exposure_context`` and
``national_exposure_context``). This module repeats that counting step for
step, so a forecast number means the same as the historical numbers:

- WorldPop 2026 1 km (``global_pop_2026_CN_1km_R2025A_UA_v1``);
- area councils (COD admin 2) expanded 500 m into the sea, made
  non-overlapping (``build_adm2_expanded``);
- the raster clipped to those, then to the dissolved scope (the three
  provinces, or the whole country) with ``all_touched=True``;
- a swath counts the pixels whose centre falls inside it.
"""

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
import shapely
import xarray as xr
from shapely.geometry import box

from src.constants import ADM1_AOI_PCODES, FJI_CRS
from src.datasources import codab
from src.utils.wind_buffers import METRIC_CRS

WORLDPOP_BLOB = "worldpop/pop_count/global_pop_2026_CN_1km_R2025A_UA_v1.tif"
# Vanuatu with room to spare (the raster is global)
VUT_BOX = (165.5, -21.5, 171.5, -12.0)


def build_adm2_expanded(adm2: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """adm2 expanded 500 m into the sea, then made non-overlapping.

    Copied from exploration/make_forecast_check_data.py (which replicates
    exploration/hist_exp.md): keep the two in step.
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


def to_wrapped(geom_metric):
    """A swath in EPSG:3832 -> the lon-wrapped frame of the raster."""
    if geom_metric is None or geom_metric.is_empty:
        return None
    return gpd.GeoSeries([geom_metric], crs=METRIC_CRS).to_crs(FJI_CRS).iloc[0]


def _count(da: xr.DataArray, geom_wrapped, union) -> int:
    if geom_wrapped is None or geom_wrapped.is_empty:
        return 0
    if not geom_wrapped.is_valid:
        geom_wrapped = shapely.make_valid(geom_wrapped)
    if not geom_wrapped.intersects(union):
        return 0
    try:
        clipped = da.rio.clip([geom_wrapped])
    except Exception:  # noqa: BLE001 — rioxarray: no pixel inside
        return 0
    return int(clipped.where(clipped > 0).sum())


class ExposureContext:
    """Boundaries and population, loaded once per run."""

    def __init__(
        self,
        adm2: gpd.GeoDataFrame | None = None,
        da_wp: xr.DataArray | None = None,
    ):
        if adm2 is None:
            adm2 = codab.load_codab_from_blob(admin_level=2)
        if da_wp is None:
            da_wp = stratus.open_blob_cog(
                WORLDPOP_BLOB, container_name="raster"
            )
            da_wp = da_wp.rio.clip_box(*VUT_BOX)
        self.adm2 = adm2
        aoi_pcodes = set(
            adm2[adm2["ADM1_PCODE"].isin(ADM1_AOI_PCODES)]["ADM2_PCODE"]
        )
        self.adm2_exp = build_adm2_expanded(adm2)
        da = da_wp.rio.clip(self.adm2_exp.geometry).squeeze(drop=True)
        da = da.compute()
        da = da.assign_coords({"x": ((da.x + 360) % 360)}).sortby("x")
        aoi = self.adm2_exp[self.adm2_exp["ADM2_PCODE"].isin(aoi_pcodes)]
        self.aoi_union = aoi.geometry.union_all()
        self.nat_union = self.adm2_exp.geometry.union_all()
        self.da_aoi = da.rio.clip([self.aoi_union], all_touched=True)
        self.da_nat = da.rio.clip([self.nat_union], all_touched=True)
        # adm2 areas in the raster's frame, for the per-area table
        self.adm2_wrapped = self.adm2_exp.to_crs(FJI_CRS)
        # provinces: the expanded area councils dissolved by province
        self.adm1_wrapped = (
            self.adm2_wrapped.merge(
                adm2[["ADM2_PCODE", "ADM1_PCODE", "ADM1_EN"]].drop_duplicates()
            )
            .dissolve(by=["ADM1_PCODE", "ADM1_EN"])
            .reset_index()
        )
        self.aoi_pop = _count(self.da_aoi, self.aoi_union, self.aoi_union)
        self.nat_pop = _count(self.da_nat, self.nat_union, self.nat_union)

    def aoi(self, geom_metric) -> int:
        """People inside the swath in Sanma, Shefa and Tafea."""
        return _count(self.da_aoi, to_wrapped(geom_metric), self.aoi_union)

    def national(self, geom_metric) -> int:
        """People inside the swath anywhere in Vanuatu."""
        return _count(self.da_nat, to_wrapped(geom_metric), self.nat_union)

    def _areas(self, level: int) -> gpd.GeoDataFrame:
        return self.adm1_wrapped if level == 1 else self.adm2_wrapped

    def by_area(self, geom_metric, level: int = 2) -> pd.Series:
        """People inside the swath per province (level 1, ADM1_PCODE) or
        area council (level 2, ADM2_PCODE)."""
        areas = self._areas(level)
        geom = to_wrapped(geom_metric)
        out = {}
        for pcode, area in zip(areas[f"ADM{level}_PCODE"], areas.geometry):
            part = None if geom is None else geom.intersection(area)
            out[pcode] = _count(self.da_nat, part, self.nat_union)
        return pd.Series(out, name="pop_exposed")

    def population(self, level: int = 2) -> pd.Series:
        """Total population per province / area council, as counted here."""
        areas = self._areas(level)
        return pd.Series(
            {
                p: _count(self.da_nat, a, self.nat_union)
                for p, a in zip(areas[f"ADM{level}_PCODE"], areas.geometry)
            },
            name="pop_total",
        )
