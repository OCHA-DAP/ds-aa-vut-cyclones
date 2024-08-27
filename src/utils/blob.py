import io
import os
import shutil
import tempfile
import zipfile
from typing import Literal

import geopandas as gpd
import pandas as pd
import rioxarray as rxr
import xarray as xr
from azure.storage.blob import ContainerClient, ContentSettings

PROD_BLOB_SAS = os.getenv("PROD_BLOB_SAS")
DEV_BLOB_SAS = os.getenv("DEV_BLOB_SAS")

PROJECT_PREFIX = "ds-aa-vut-cyclones"


def get_container_client(
    container_name: str = "projects", stage: Literal["prod", "dev"] = "dev"
):
    sas = DEV_BLOB_SAS if stage == "dev" else PROD_BLOB_SAS
    container_url = (
        f"https://imb0chd0{stage}.blob.core.windows.net/"
        f"{container_name}?{sas}"
    )
    return ContainerClient.from_container_url(container_url)


def upload_parquet_to_blob(
    blob_name,
    df,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
    **kwargs,
):
    upload_blob_data(
        blob_name,
        df.to_parquet(index=False, **kwargs),
        stage=stage,
        container_name=container_name,
    )


def load_parquet_from_blob(
    blob_name,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
):
    blob_data = load_blob_data(
        blob_name, stage=stage, container_name=container_name
    )
    return pd.read_parquet(io.BytesIO(blob_data))


def upload_csv_to_blob(
    blob_name,
    df,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
    **kwargs,
):
    upload_blob_data(
        blob_name,
        df.to_csv(index=False, **kwargs),
        stage=stage,
        content_type="text/csv",
        container_name=container_name,
    )


def load_csv_from_blob(
    blob_name,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
    **kwargs,
):
    blob_data = load_blob_data(
        blob_name, stage=stage, container_name=container_name
    )
    return pd.read_csv(io.BytesIO(blob_data), **kwargs)


def upload_gdf_to_blob(gdf, blob_name, stage: Literal["prod", "dev"] = "dev"):
    with tempfile.TemporaryDirectory() as temp_dir:
        # File paths for shapefile components within the temp directory
        shp_base_path = os.path.join(temp_dir, "data")

        gdf.to_file(shp_base_path, driver="ESRI Shapefile")

        zip_file_path = os.path.join(temp_dir, "data")

        shutil.make_archive(
            base_name=zip_file_path, format="zip", root_dir=temp_dir
        )

        # Define the full path to the zip file
        full_zip_path = f"{zip_file_path}.zip"

        # Upload the buffer content as a blob
        with open(full_zip_path, "rb") as data:
            upload_blob_data(blob_name, data, stage=stage)


def load_gdf_from_blob(
    blob_name, shapefile: str = None, stage: Literal["prod", "dev"] = "dev"
):
    blob_data = load_blob_data(blob_name, stage=stage)
    with zipfile.ZipFile(io.BytesIO(blob_data), "r") as zip_ref:
        zip_ref.extractall("temp")
        if shapefile is None:
            shapefile = [f for f in zip_ref.namelist() if f.endswith(".shp")][
                0
            ]
        gdf = gpd.read_file(f"temp/{shapefile}")
    return gdf


def load_blob_data(
    blob_name,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
):
    container_client = get_container_client(
        stage=stage, container_name=container_name
    )
    blob_client = container_client.get_blob_client(blob_name)
    data = blob_client.download_blob().readall()
    return data


def upload_blob_data(
    blob_name,
    data,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
    content_type: str = None,
):
    container_client = get_container_client(
        stage=stage, container_name=container_name
    )

    if content_type is None:
        content_settings = ContentSettings(
            content_type="application/octet-stream"
        )
    else:
        content_settings = ContentSettings(content_type=content_type)

    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(
        data, overwrite=True, content_settings=content_settings
    )


def list_container_blobs(
    name_starts_with=None,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
):
    container_client = get_container_client(
        stage=stage, container_name=container_name
    )
    return [
        blob.name
        for blob in container_client.list_blobs(
            name_starts_with=name_starts_with
        )
    ]


def get_blob_url(
    blob_name,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
):
    container_client = get_container_client(
        stage=stage, container_name=container_name
    )
    blob_client = container_client.get_blob_client(blob_name)
    return blob_client.url


def open_blob_cog(
    blob_name,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
    chunks=None,
):
    cog_url = get_blob_url(
        blob_name, stage=stage, container_name=container_name
    )
    if chunks is None:
        chunks = True
    return rxr.open_rasterio(cog_url, chunks=chunks)


def upload_cog_to_blob(
    blob_name: str,
    da: xr.DataArray,
    stage: Literal["prod", "dev"] = "dev",
    container_name: str = "projects",
):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tif") as tmpfile:
        temp_filename = tmpfile.name
        da.rio.to_raster(temp_filename, driver="COG")
        with open(temp_filename, "rb") as f:
            get_container_client(
                container_name=container_name, stage=stage
            ).get_blob_client(blob_name).upload_blob(f, overwrite=True)
