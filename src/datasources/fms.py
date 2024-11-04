import base64
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

from src.constants import FJI_CRS, LOCAL_TIMEZONE, OUTPUT_DIR
from src.datasources.codab import load_codab_from_blob
from src.utils import blob


def decode_forecast_csv(csv: str) -> StringIO:
    """Decodes encoded string of CSV.

    Parameters
    ----------
    csv: str
        String of CSV (received as command line argument of script)

    Returns
    -------
    StringIO
        StringIO of CSV, to be used in process_fms_forecast()
    """
    bytes_str = csv.encode("ascii") + b"=="
    converted_bytes = base64.b64decode(bytes_str)
    csv_str = converted_bytes.decode("ascii")
    filepath = StringIO(csv_str)
    return filepath


def process_fms_forecast(
    path: Path | StringIO, save_local: bool = True, save_blob: bool = True
) -> gpd.GeoDataFrame:
    """Loads FMS raw forecast in default CSV export format from FMS cyclone
    forecast software.
    Parameters
    ----------
    path: Path | StringIO
        Path to raw forecast CSV. Path can be a StringIO
        (so CSV can be passed as an encoded string from Power Automate)
    save_local: bool = True
        If True, saves forecast as CSV
    save_blob: bool = True
        If True, saves forecast to blob

    Returns
    -------
    DataFrame of processed forecast
    """
    df_date = pd.read_csv(path, header=None, nrows=3)
    date_str = df_date.iloc[0, 1].removeprefix("baseTime=")
    base_time = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    cyclone_name = (
        df_date.iloc[2, 0].removeprefix("# CycloneName=").capitalize()
    )
    if isinstance(path, StringIO):
        path.seek(0)
    df_data = pd.read_csv(
        path,
        skiprows=range(6),
    )
    df_data = df_data.drop([0])
    df_data = df_data.rename(
        columns={"Time[fmt=yyyy-MM-dd'T'HH:mm:ss'Z']": "forecast_time"}
    )
    df_data["forecast_time"] = pd.to_datetime(
        df_data["forecast_time"]
    ).dt.tz_localize(None)
    df_data["cyclone_name"] = cyclone_name
    df_data["base_time"] = base_time
    df_data["season"] = datetime_to_season(base_time)
    df_data["Name Season"] = df_data["cyclone_name"] + " " + df_data["season"]
    df_data["leadtime"] = df_data["forecast_time"] - df_data["base_time"]
    df_data["leadtime"] = (
        df_data["leadtime"].dt.days * 24
        + df_data["leadtime"].dt.seconds / 3600
    ).astype(int)
    df_data["Category"] = df_data["Category"].fillna(0)
    df_data["Category"] = df_data["Category"].astype(int, errors="ignore")
    base_time_file_str = base_time.isoformat(timespec="minutes").replace(
        ":", ""
    )
    if save_local:
        df_data.to_csv(
            OUTPUT_DIR / f"forecast_{base_time_file_str}.csv", index=False
        )
    if save_blob:
        blob_name = (
            f"{blob.PROJECT_PREFIX}/raw/fms/2024_2025/forecast_"
            f"{base_time_file_str}.parquet"
        )
        blob.upload_blob_data(blob_name, df_data)
    gdf = gpd.GeoDataFrame(
        df_data,
        geometry=gpd.points_from_xy(df_data["Longitude"], df_data["Latitude"]),
    )
    gdf = gdf.set_crs(FJI_CRS)
    return gdf


def datetime_to_season(date):
    # July 1 (182nd day of the year) is technically the start of the season
    eff_date = date - pd.Timedelta(days=182)
    return f"{eff_date.year}/{eff_date.year + 1}"


def str_from_report(report: dict) -> dict:
    """Produce relevant

    Parameters
    ----------
    report: dict
        Dict of forecast report

    Returns
    -------
    dict with {
        "file_dt_str": UTC datetime formatted for filename,
        "vut_time": Vanuatu time for plots,
        "vut_date": Vanuatu date for plots,
    }
    """
    utc_str = report.get("publication_time")
    utc = datetime.fromisoformat(utc_str)
    vut_tz = utc.astimezone(ZoneInfo(LOCAL_TIMEZONE))
    vut_time_str = vut_tz.isoformat(timespec="minutes").split("+")[0]
    vut_time_split = vut_time_str.split("T")
    return {
        "file_dt_str": f'{utc_str.replace(":", "").split("+")[0]}Z',
        "vut_time": vut_time_split[1],
        "vut_date": vut_time_split[0],
    }


def get_report(forecast: gpd.GeoDataFrame) -> dict:
    """Gets trigger report

    Parameters
    ----------
    forecast: pd.DataFrame
        df of processed forecast

    Returns
    -------
    dict
        Dict of cyclone name, forecast publication time (UTC)
    """

    cyclone = forecast.iloc[0]["Name Season"]
    base_time = forecast.iloc[0]["base_time"]
    base_time = base_time.replace(tzinfo=timezone.utc)
    base_time_str = base_time.isoformat(timespec="minutes")
    report = {
        "cyclone": cyclone,
        "publication_time": base_time_str,
    }
    return report


def calculate_distances(
    report: dict, forecast: gpd.GeoDataFrame, save: bool = True
) -> pd.DataFrame:
    """Calculates distances from TC forecast track to admin2 and admin3.
    The value of the distance is the distance of a LineString of the TC track
    to each admin area. For a track passing directly over an admin level, the
    distance would be 0.
    The uncertainty of the distance is the value of the uncertainty cone of
    the forecast, at the forecasted point that is closest to the admin area.
    This is a somewhat crude approximation for the uncertainty, but it's good
    enough for now.

    Parameters
    ----------
    report: dict
        Dict of forecast report
    forecast: gpd.GeoDataFrame
        GeoDF of forecast
    save: bool = True
        If True, saves CSV of distances

    Returns
    -------
    pd.DataFrame of distances to adm2
    """
    report_str = str_from_report(report)
    forecast = forecast.to_crs(3832)
    forecast = forecast[forecast["leadtime"] <= 120]
    track = LineString([(p.x, p.y) for p in forecast.geometry])
    return_df = pd.DataFrame()
    for level in [1, 2]:
        adm = load_codab_from_blob(admin_level=level).to_crs(3832)
        cols = [
            "ADM1_PCODE",
            "ADM1_EN",
            "geometry",
        ]
        if level == 2:
            cols.extend(["ADM2_PCODE", "ADM2_EN"])
        distances = adm[cols].copy()
        distances["distance (km)"] = np.round(
            track.distance(adm.geometry) / 1000
        ).astype(int)
        distances["uncertainty (km)"] = None
        distances["category"] = None
        # find closest point to use for uncertainty
        for i, row in distances.iterrows():
            forecast["distance"] = row.geometry.distance(forecast.geometry)
            i_min = forecast["distance"].idxmin()
            distances.loc[i, "uncertainty (km)"] = np.round(
                forecast.loc[i_min, "Uncertainty"]
            ).astype(int)
            distances.loc[i, "category"] = forecast.loc[i_min, "Category"]
        distances = distances.drop(columns="geometry")
        distances = distances.sort_values("distance (km)")
        if save:
            distances.to_csv(
                OUTPUT_DIR
                / f"distances_adm{level}_{report_str.get('file_dt_str')}.csv",
                index=False,
            )
        if level == 1:
            return_df = distances.copy()
    return return_df
