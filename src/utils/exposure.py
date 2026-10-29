import geopandas as gpd
import pandas as pd
import xarray as xr
from rioxarray.exceptions import NoDataInBounds
from tqdm.auto import tqdm


def calculate_single_adm_exposure(
    gdf_buffers: gpd.GeoDataFrame,
    da_wp: xr.DataArray,
    geo_crs: str = "EPSG:4326",
) -> pd.DataFrame:
    # ensure correct CRS
    gdf_buffers = gdf_buffers.to_crs(geo_crs)
    da_wp = da_wp.assign_coords({"x": ((da_wp.x + 360) % 360)}).sortby("x")

    records = []
    for _, row in gdf_buffers.iterrows():
        row_data = row.drop(labels="geometry").to_dict()

        if not row.geometry or row.geometry.is_empty:
            pop_exposed = 0
        else:
            try:
                da_wp_clip_buffer = da_wp.rio.clip([row.geometry])
                pop_exposed = int(
                    da_wp_clip_buffer.where(da_wp_clip_buffer > 0).sum()
                )
            except NoDataInBounds:
                pop_exposed = 0

        row_data["pop_exposed"] = pop_exposed
        records.append(row_data)

    return pd.DataFrame(records)


def calculate_multi_adm_exposure(
    gdf_buffers: gpd.GeoDataFrame,
    da_wp: xr.DataArray,
    gdf_adm: gpd.GeoDataFrame,
    adm_index: str = "ADM3_PCODE",
    disable_tqdm: bool = True,
    geo_crs: str = "EPSG:4326",
) -> pd.DataFrame:
    # ensure correct CRS
    gdf_buffers = gdf_buffers.to_crs(geo_crs)
    gdf_adm = gdf_adm.to_crs(geo_crs)
    da_wp = da_wp.assign_coords({"x": ((da_wp.x + 360) % 360)}).sortby("x")

    dfs = []
    for _, adm_row in tqdm(
        gdf_adm.iterrows(), total=len(gdf_adm), disable=disable_tqdm
    ):
        # note that we have to set all_touched=True here to ensure that
        # all possible pixels are grabbed and the sum over all the
        # buffers is correct (all_touched is then False in the admin
        # aggregation to avoid double counting)
        da_wp_adm = da_wp.rio.clip([adm_row.geometry], all_touched=True)
        _df_exp = calculate_single_adm_exposure(gdf_buffers, da_wp_adm)
        _df_exp[adm_index] = adm_row[adm_index]
        dfs.append(_df_exp)
    df_exp = pd.concat(dfs, ignore_index=True)
    return df_exp
