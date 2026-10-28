"""The observed track of a storm, from the forecasts we receive.

We only receive forecast CSVs, so the observed track is the analysis point
(lead time 0: the storm's position and wind radii at issue time) of every
forecast received for the storm, as ds-storms-pipeline does for NHC
(nhc_tracks_obsv_buffers). Each run adds its analysis point to a record on
the dev blob and reads back all points for that disturbance. There is one
record per source: VMHS's is the framework's observed track (the
observational trigger); RSMC Nadi's is only drawn on the maps in the FMS
emails.

Test runs (TEST_EMAIL or SIMULATE_TRIGGER) keep a separate record, so a
test CSV can never become part of a real storm's observed track. Under
DRY_RUN nothing is written.
"""

import ocha_stratus as stratus
import pandas as pd
from azure.core.exceptions import ResourceNotFoundError

from src.constants import PROJECT_PREFIX
from src.utils.wind_buffers import QUADS

SOURCES = ("fms", "vmhs")
RADII_COLS = [f"r{s}_{q}" for s in (34, 50, 64) for q in QUADS]
COLUMNS = [
    "disturbance_id",
    "season",
    "cyclone_name",
    "valid_time",
    "lat",
    "lon",
    *RADII_COLS,
    "content_sha12",
]


def record_blob(test: bool, source: str = "vmhs") -> str:
    """monitoring/{source}_observed_points.csv (test_... for test runs)."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    prefix = "test_" if test else ""
    return f"{PROJECT_PREFIX}/monitoring/{prefix}{source}_observed_points.csv"


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Key columns with one type whatever the source (CSV text or a fresh
    point), so de-duplication and look-ups match."""
    df = df.reindex(columns=COLUMNS).copy()
    df["disturbance_id"] = df["disturbance_id"].astype(str)
    df["season"] = pd.to_numeric(df["season"]).astype("Int64")
    df["valid_time"] = pd.to_datetime(df["valid_time"])
    return df


def load_points(test: bool, source: str = "vmhs") -> pd.DataFrame:
    try:
        # as text: a numeric-looking id must not become a number
        df = stratus.load_csv_from_blob(
            record_blob(test, source), stage="dev", dtype=str
        )
    except ResourceNotFoundError:
        return pd.DataFrame(columns=COLUMNS)
    return _normalise(df)


def save_points(df: pd.DataFrame, test: bool, source: str = "vmhs") -> None:
    stratus.upload_csv_to_blob(df, record_blob(test, source), stage="dev")


# a first point this close to the issue time counts as the analysis
ANALYSIS_TOLERANCE_H = 3


def analysis_point(forecast, content_sha12: str) -> pd.DataFrame:
    """The forecast's analysis row (lead time 0, or the first point if it
    is within ANALYSIS_TOLERANCE_H of it), in the record's columns."""
    track = forecast.track.sort_values("leadtime")
    first = track.iloc[[0]]
    if abs(float(first["leadtime"].iloc[0])) > ANALYSIS_TOLERANCE_H:
        raise ValueError(
            "the forecast has no analysis point (its first point is "
            f"{first['leadtime'].iloc[0]:.1f} h after issue)"
        )
    row = first.copy()
    row["disturbance_id"] = forecast.disturbance_id
    row["season"] = forecast.season
    row["cyclone_name"] = forecast.cyclone_name
    row["content_sha12"] = content_sha12
    return row.reindex(columns=COLUMNS)


def add_point(points: pd.DataFrame, point: pd.DataFrame) -> pd.DataFrame:
    """Add a point; a later forecast for the same storm and time replaces
    the earlier one (a corrected re-issue)."""
    key = ["disturbance_id", "season", "valid_time"]
    frames = [_normalise(f) for f in (points, point) if not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(subset=key, keep="last").reset_index(drop=True)


def observed_track(
    points: pd.DataFrame,
    disturbance_id: str,
    season: int,
    until: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """The analysis points recorded for one storm, oldest first; with
    ``until``, only those up to that time (the forecast's issue time), as
    ds-storms-pipeline's cumulative observed buffers: a forecast handled
    late or out of order never sees positions from after it was issued."""
    sel = points[
        (points["disturbance_id"].astype(str) == str(disturbance_id))
        & (pd.to_numeric(points["season"]) == season)
    ].copy()
    if until is not None:
        sel = sel[pd.to_datetime(sel["valid_time"]) <= pd.Timestamp(until)]
    # numeric whatever the record's dtypes (an empty first record is object)
    for col in ["lat", "lon", *RADII_COLS]:
        sel[col] = pd.to_numeric(sel[col]).astype(float)
    sel["valid_time"] = pd.to_datetime(sel["valid_time"])
    return sel.sort_values("valid_time").reset_index(drop=True)
