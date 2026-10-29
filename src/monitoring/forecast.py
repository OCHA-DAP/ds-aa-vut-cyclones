"""Read a TC Module forecast CSV (RSMC Nadi / FMS, and VMHS) into a track.

Both met services run the same Tropical Cyclone Module and export the same
CSV: three ``#`` header lines (track type and base time, disturbance id,
cyclone name), a column-heading line starting ``Time[fmt=...]``, then one
row per forecast point. VMHS had not sent a sample when this was written;
the format is assumed to match FMS's (see the README).

The track carries quadrant wind radii in the column names the trigger
design uses (``r{speed}_{quad}``, nautical miles), so the swaths are built
with :func:`src.utils.wind_buffers.wind_buffers_from_track`, exactly as for
the trigger analysis. FMS and VMHS radii are already 10-minute winds, so
the 64 kt contour is read as is (no V_EQ conversion, unlike JTWC).
"""

import io
import re
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from src.constants import FJI_CRS
from src.utils.wind_buffers import QUADS

OFFICIAL_TRACK = "Official Forecast Track"
SPEEDS = (34, 50, 64)
SPEED2WORD = {34: "Gale", 50: "Storm", 64: "Hurricane"}
# symmetric radius, used when no quadrant radius is given for that speed
SPEED2FALLBACK = {34: "GaleRadius", 50: "StormRadius", 64: "HurricaneRadius"}
# azimuthal equidistant around Vanuatu: true distances from the country
VUT_AEQD = "+proj=aeqd +lat_0=-16.2 +lon_0=167.7 +datum=WGS84 +units=m"
NM_TO_M = 1852.0


class ForecastParseError(ValueError):
    """The CSV can't be read as a forecast track."""


@dataclass
class Forecast:
    track: pd.DataFrame
    base_time: pd.Timestamp  # UTC, tz-naive
    cyclone_name: str
    disturbance_id: str
    track_type: str
    season: int


def datetime_to_season(dt: pd.Timestamp) -> int:
    """South Pacific season: July 2025 to June 2026 is season 2026."""
    return dt.year + 1 if dt.month >= 7 else dt.year


def _header_value(lines: list[str], key: str) -> str:
    for line in lines:
        for part in line.lstrip("#").split(","):
            name, _, value = part.strip().partition("=")
            if name.strip().lower() == key.lower():
                return value.strip()
    raise ForecastParseError(f"no {key} in the CSV header")


def _numeric(df: pd.DataFrame, col: str, required: bool) -> pd.Series:
    """Column as floats. Empty and 'NaN' cells are missing; any other
    unreadable cell is an error, so a corrupted radius can never quietly
    shrink the swath."""
    if col not in df.columns:
        if required:
            raise ForecastParseError(f"no {col} column")
        return pd.Series(np.nan, index=df.index)
    raw = df[col].astype(object)
    raw = raw.where(raw.notna(), "").astype(str).str.strip()
    values = pd.to_numeric(raw, errors="coerce").astype(float)
    blank = raw.str.lower().isin(["", "nan", "none"])
    bad = values.isna() & ~blank
    bad |= ~np.isfinite(values.fillna(0))
    if bad.any():
        raise ForecastParseError(f"unreadable value in {col}")
    if required and values.isna().any():
        raise ForecastParseError(f"missing value in {col}")
    return values


def parse_forecast_csv(data: bytes) -> Forecast:
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header = [line for line in lines[:10] if line.startswith("#")]
    track_type = _header_value(header, "trackType")
    base_time = pd.Timestamp(_header_value(header, "baseTime"))
    if base_time.tzinfo is not None:
        base_time = base_time.tz_convert("UTC").tz_localize(None)
    disturbance_id = _header_value(header, "DisturbanceId")
    cyclone_name = _header_value(header, "CycloneName").capitalize()

    starts = [i for i, line in enumerate(lines) if line.startswith("Time[")]
    if not starts:
        raise ForecastParseError("no 'Time[...]' column heading line")
    heading, rest = lines[starts[0]], lines[starts[0] + 1 :]  # noqa: E203
    body = "\n".join(
        [heading] + [x for x in rest if x and not x.startswith("#")]
    )
    df = pd.read_csv(io.StringIO(body), dtype=str)
    if df.empty:
        raise ForecastParseError("no forecast points")
    time_col = df.columns[0]
    valid_time = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    if valid_time.isna().any():
        raise ForecastParseError("unreadable forecast time")

    track = pd.DataFrame(
        {
            "valid_time": valid_time.dt.tz_localize(None),
            "lat": _numeric(df, "Latitude", required=True),
            "lon": _numeric(df, "Longitude", required=True) % 360,
            "category": _numeric(df, "Category", required=False),
            "mean_wind": _numeric(df, "MeanWind", required=False),
            "uncertainty_nm": _numeric(df, "Uncertainty", required=False),
        }
    )
    for speed in SPEEDS:
        word = SPEED2WORD[speed]
        quads = {
            q: _numeric(df, f"{q.upper()}{word}Radius", required=False)
            for q in QUADS
        }
        fallback = _numeric(df, SPEED2FALLBACK[speed], required=False)
        no_quads = pd.concat(quads.values(), axis=1).isna().all(axis=1)
        for q, values in quads.items():
            # Zero, not NaN, where there is no radius: the track is
            # interpolated before the swath is built, and pandas carries a
            # trailing NaN forward as the last radius.
            track[f"r{speed}_{q}"] = (
                values.where(~no_quads, fallback).fillna(0).clip(lower=0)
            )
    track["leadtime"] = (
        track["valid_time"] - base_time
    ).dt.total_seconds() / 3600
    track = track.sort_values("valid_time").reset_index(drop=True)
    return Forecast(
        track=track,
        base_time=base_time,
        cyclone_name=cyclone_name,
        disturbance_id=disturbance_id,
        track_type=track_type,
        season=datetime_to_season(base_time),
    )


STEP_H = 0.5  # 30-minute track, as in the trigger design


def _with_leadtime(track: pd.DataFrame) -> pd.DataFrame:
    """Hours since the first point as ``leadtime`` if the track has none
    (the observed track)."""
    if "leadtime" in track.columns:
        return track
    first = track["valid_time"].min()
    return track.assign(
        leadtime=(track["valid_time"] - first).dt.total_seconds() / 3600
    )


def _interp_at(track: pd.DataFrame, leads: np.ndarray) -> pd.DataFrame:
    """The track at the given lead times (hours), linear in every numeric
    column, with no extrapolation.

    np.interp rather than pandas' resample().interpolate(): on pandas 2.x
    (the Databricks lock) that returns all-NaN for a track whose times are
    off the :00/:30 clock, which would count as zero exposure.
    """
    t = (
        _with_leadtime(track)
        .sort_values("valid_time")
        .drop_duplicates(subset=["valid_time"], keep="last")
        .dropna(subset=["lat", "lon"])
    )
    if t.empty:
        return t.reset_index(drop=True)
    lt = t["leadtime"].to_numpy(float)
    leads = np.unique(leads[(leads >= lt.min()) & (leads <= lt.max())])
    first_time = t["valid_time"].iloc[0] - pd.to_timedelta(lt[0], unit="h")
    out = pd.DataFrame({"leadtime": leads})
    out["valid_time"] = first_time + pd.to_timedelta(leads, unit="h")
    numeric = t.select_dtypes(include=[np.number]).columns
    for col in numeric.drop("leadtime"):
        out[col] = np.interp(leads, lt, t[col].to_numpy(float))
    if out[["lat", "lon"]].isna().any().any():
        raise ForecastParseError("track interpolation produced NaN positions")
    return out


def interpolated(track: pd.DataFrame) -> pd.DataFrame:
    """The track every 30 minutes of lead time, plus its own points."""
    lt = _with_leadtime(track)["leadtime"].to_numpy(float)
    if lt.size == 0:
        return track.iloc[0:0].reset_index(drop=True)
    start = np.ceil(lt.min() / STEP_H) * STEP_H
    grid = np.arange(start, lt.max() + 1e-9, STEP_H)
    return _interp_at(track, np.concatenate([grid, lt]))


def segment(track: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    """The part of the track between lead times ``lo`` and ``hi`` hours,
    every 30 minutes, starting and ending exactly there (where the forecast
    reaches)."""
    lt = _with_leadtime(track)["leadtime"].to_numpy(float)
    inside = lt[(lt >= lo) & (lt <= hi)]
    grid = np.concatenate([np.arange(lo, hi, STEP_H), [hi], inside])
    return _interp_at(track, grid)


def min_distance_km(
    track: pd.DataFrame, country: gpd.GeoDataFrame, max_leadtime: float
) -> float:
    """Closest approach of the track (up to ``max_leadtime``) to the
    country's land, in km."""
    interp = interpolated(track)
    interp = interp[interp["leadtime"] <= max_leadtime + 1e-6]
    if interp.empty:
        return float("inf")
    land = country.to_crs(VUT_AEQD).union_all()
    points = gpd.GeoSeries(
        [Point(xy) for xy in zip(interp["lon"], interp["lat"])], crs=FJI_CRS
    ).to_crs(VUT_AEQD)
    return float(points.distance(land).min()) / 1000


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_()-]", "_", value.lower().replace(" ", "_"))
