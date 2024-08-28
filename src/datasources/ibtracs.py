import os
import urllib.request
from pathlib import Path
from typing import Literal

import geopandas as gpd
import xarray as xr

from src.datasources import codab
from src.utils import blob

DATA_DIR = Path(os.getenv("AA_DATA_DIR_NEW"))
IBTRACS_RAW_DIR = DATA_DIR / "public" / "raw" / "glb" / "ibtracs"


def download_ibtracs(dataset: Literal["ALL", "last3years"] = "ALL"):
    """Download IBTrACS data."""
    url = (
        "https://www.ncei.noaa.gov/data/"
        "international-best-track-archive-for-climate-stewardship-ibtracs/"
        f"v04r00/access/netcdf/IBTrACS.{dataset}.v04r00.nc"
    )
    download_path = IBTRACS_RAW_DIR / f"IBTrACS.{dataset}.v04r00.nc"
    urllib.request.urlretrieve(url, download_path)


def load_all_ibtracs():
    """Load IBTrACS data from NetCDF file."""
    load_path = IBTRACS_RAW_DIR / "IBTrACS.ALL.v04r00.nc"
    return xr.load_dataset(load_path)


def process_all_ibtracs(wind_provider: Literal["usa", "wmo"] = "wmo"):
    ds = load_all_ibtracs()
    subset_vars = ["sid", f"{wind_provider}_wind", "name", "basin"]
    ds_subset = ds[subset_vars]
    str_vars = ["name", "sid", "basin"]
    ds_subset[str_vars] = ds_subset[str_vars].astype(str)
    ds_subset["time"] = ds_subset["time"].astype("datetime64[s]")
    df = ds_subset.to_dataframe().dropna().reset_index()
    cols = subset_vars + ["time", "lat", "lon"]
    df = df[cols]
    filestem = f"ibtracs_with_{wind_provider}_wind"
    df["row_id"] = df.index
    blob_name = f"ibtracs/{filestem}.parquet"
    blob.upload_parquet_to_blob(
        blob_name, df, stage="dev", container_name="global"
    )


def load_ibtracs_with_wind(wind_provider: Literal["usa", "wmo"] = "wmo"):
    """Load IBTrACS data with wind speed data from a specific provider."""
    blob_name = f"ibtracs/ibtracs_with_{wind_provider}_wind.parquet"
    df = blob.load_parquet_from_blob(
        blob_name, stage="dev", container_name="global"
    )
    return df


def process_vut_distances():
    adm = codab.load_codab_from_blob()
    df = load_ibtracs_with_wind(wind_provider="usa")
    df_sp = df[df["basin"] == "SP"]

    def resample_and_interpolate(group):
        group = group.set_index("time")
        group = group.resample("30min").interpolate(method="linear")
        return group

    cols = ["usa_wind", "lat", "lon", "time"]
    df_interp = (
        df_sp.groupby(["sid", "name", "basin"])[cols]
        .apply(resample_and_interpolate, include_groups=False)
        .reset_index()
    )
    gdf = gpd.GeoDataFrame(
        df_interp,
        geometry=gpd.points_from_xy(df_interp.lon, df_interp.lat),
        crs="EPSG:4326",
    )
    gdf["vut_distance_km"] = (
        gdf.to_crs(3832).geometry.distance(adm.to_crs(3832).iloc[0].geometry)
        / 1000
    )
    blob_name = f"{blob.PROJECT_PREFIX}/processed/vut_distances.parquet"
    blob.upload_parquet_to_blob(
        blob_name, gdf.drop(columns="geometry"), stage="dev"
    )


def load_vut_distances():
    blob_name = f"{blob.PROJECT_PREFIX}/processed/vut_distances.parquet"
    return blob.load_parquet_from_blob(blob_name, stage="dev")


def knots2cat(knots: float) -> int:
    """
    Convert from knots to Category (Australian scale)
    Parameters
    ----------
    knots: float
        Wind speed in knots

    Returns
    -------
    Category
    """
    category = 0
    if knots > 107:
        category = 5
    elif knots > 85:
        category = 4
    elif knots > 63:
        category = 3
    elif knots > 47:
        category = 2
    elif knots > 33:
        category = 1
    return category
