"""Build wind-swath buffers from quadrant wind radii.

Adapted from ``pa-aa-fji-storms`` (``src/datasources/ibtracs.py``) so that
forecast buffers are constructed the same way as the observed IBTrACS
USA-radii buffers already used for this framework.
"""

from typing import Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from src.constants import FJI_CRS

NM_TO_M = 1852.0
QUADS = ("ne", "se", "sw", "nw")
# metric CRS for the Pacific (EPSG:3832, Pacific Mercator)
METRIC_CRS = 3832


def _radius_from_quadrants(
    theta_deg: np.ndarray, ne: float, se: float, sw: float, nw: float
) -> np.ndarray:
    """Radius at each bearing, linearly interpolated between quadrant values.

    Control bearings (0 deg = East, 90 deg = North): 45 NE, 135 NW,
    225 SW, 315 SE.
    """
    bearings = np.array([45, 135, 225, 315, 405], dtype=float)
    radii = np.array([ne, nw, sw, se, ne], dtype=float)
    t = (np.asarray(theta_deg) % 360).astype(float)
    t_wrap = t.copy()
    t_wrap[t < 45] += 360
    return np.interp(t_wrap, bearings, radii)


def make_quadrant_disk(
    center_xy: Tuple[float, float],
    ne: float,
    se: float,
    sw: float,
    nw: float,
    n_points: int = 180,
) -> Polygon:
    """Smooth polygon around ``center_xy`` from quadrant radii (metres)."""
    x0, y0 = center_xy
    theta = np.linspace(0, 360, n_points, endpoint=False)
    r = _radius_from_quadrants(theta, ne, se, sw, nw)
    th = np.deg2rad(theta)
    return Polygon(np.column_stack([x0 + r * np.cos(th), y0 + r * np.sin(th)]))


def interpolate_track(
    df: pd.DataFrame,
    time_col: str = "valid_time",
    lat_col: str = "lat",
    lon_col: str = "lon",
    freq: str = "30min",
) -> pd.DataFrame:
    """Resample a track to a regular time grid, interpolating all numerics.

    Longitude is assumed already in [0, 360). No extrapolation.
    """
    work = df.copy()
    work[time_col] = pd.to_datetime(work[time_col], utc=True)
    work = (
        work.sort_values(time_col)
        .drop_duplicates(subset=[time_col], keep="first")
        .dropna(subset=[lat_col, lon_col])
    )
    if len(work) <= 1:
        return work.reset_index(drop=True)

    num_cols = work.select_dtypes(include=[np.number]).columns.tolist()
    out = (
        work.set_index(time_col)[num_cols]
        .resample(freq)
        .interpolate(method="linear")
        .reset_index()
    )
    return out


def build_merged_wind_buffer(
    gdf_points: gpd.GeoDataFrame, quad_cols: Tuple[str, str, str, str]
):
    """Union of quadrant disks along a track. ``None`` if no radii present."""
    ne_col, se_col, sw_col, nw_col = quad_cols
    cols = [ne_col, se_col, sw_col, nw_col]
    if not set(cols).issubset(gdf_points.columns):
        return None
    vals = gdf_points[cols].fillna(0)
    if (vals.to_numpy() <= 0).all():
        return None

    polys = []
    for (_, row), (_, rad) in zip(gdf_points.iterrows(), vals.iterrows()):
        if (rad <= 0).all():
            continue
        polys.append(
            make_quadrant_disk(
                (row.geometry.x, row.geometry.y),
                rad[ne_col] * NM_TO_M,
                rad[se_col] * NM_TO_M,
                rad[sw_col] * NM_TO_M,
                rad[nw_col] * NM_TO_M,
            )
        )
    if not polys:
        return None
    return gpd.GeoSeries(polys, crs=METRIC_CRS).union_all()


def wind_buffers_from_track(
    df: pd.DataFrame,
    speeds: Tuple[int, ...] = (34, 50, 64),
    quad_fmt: str = "r{speed}_{quad}",
    lat_col: str = "lat",
    lon_col: str = "lon",
    time_col: str = "valid_time",
) -> gpd.GeoDataFrame:
    """Wind-swath buffer per speed threshold for a single track.

    The track is interpolated to 30-minute steps first, so the swath is a
    continuous sweep rather than a string of disks at synoptic times.
    """
    quad_cols = [
        quad_fmt.format(speed=s, quad=q) for s in speeds for q in QUADS
    ]
    keep = [c for c in quad_cols if c in df.columns]
    work = df[[lat_col, lon_col, time_col] + keep].copy()
    work[lon_col] = (work[lon_col] + 360) % 360

    interp = interpolate_track(
        work, time_col=time_col, lat_col=lat_col, lon_col=lon_col
    )
    if interp.empty:
        return gpd.GeoDataFrame(
            {"buffer_speed": []}, geometry=[], crs=METRIC_CRS
        )

    gdf = gpd.GeoDataFrame(
        interp,
        geometry=gpd.points_from_xy(interp[lon_col], interp[lat_col]),
        crs=FJI_CRS,
    ).to_crs(METRIC_CRS)

    recs, geoms = [], []
    for speed in speeds:
        cols = tuple(quad_fmt.format(speed=speed, quad=q) for q in QUADS)
        geom = build_merged_wind_buffer(gdf, cols)
        if geom is None or geom.is_empty:
            continue
        recs.append({"buffer_speed": speed})
        geoms.append(geom)

    if not recs:
        return gpd.GeoDataFrame(
            {"buffer_speed": []}, geometry=[], crs=METRIC_CRS
        )
    return gpd.GeoDataFrame(recs, geometry=geoms, crs=METRIC_CRS)
