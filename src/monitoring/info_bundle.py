"""One information email per forecast time, for both providers.

VMHS and RSMC Nadi (FMS) issue forecasts on the same six-hourly schedule,
but their CSVs reach us separately. Each forecast's run evaluates it, sends
its trigger emails at once (those never wait), saves what its part of the
information email needs (the "context") and adds a row to a small index on
the dev blob. The information email then goes out:

- with both forecasts, as soon as the second one for the same storm and
  time arrives: the VMHS forecast in full (maps, likely range,
  attachments), RSMC Nadi's readiness result as a few lines;
- with one, if its partner hasn't arrived WAIT_HOURS after it did (with
  both, if the partner is in by then). The next forecast run of either
  source sends these, and so does a "flush" run, which the Fiji poller
  (pa-aa-fji-storms) starts when the index has a row past its deadline.
  On the real lists, one whose forecast is older than
  MAX_FORECAST_AGE_HOURS expires instead;
- again with both, if a partner arrives after the first one went out on
  its own, or a forecast is re-issued after its email went out.

Two forecasts are of the same storm and time if they were issued at most
MATCH_HOURS apart and have the same disturbance id, the same name, or
analysis positions at most MATCH_KM apart (early systems have no name, and
VMHS's ids are unknown until it sends a CSV).

A run writes its row before sending anything and marks each email's rows
sent once it went out, so a failure leaves them waiting: the next run sends
them again, and the email record (email_record.py) stops a repeat. Test
runs use their own index and contexts (test_...). Under DRY_RUN nothing is
written.
"""

import json
import math
import os
import re

import ocha_stratus as stratus
import pandas as pd
from azure.core.exceptions import ResourceNotFoundError

from src.constants import PROJECT_PREFIX

MATCH_HOURS = 3
MATCH_KM = 300
WAIT_HOURS = float(os.getenv("INFO_WAIT_HOURS", "2"))
KEEP_DAYS = 5  # index rows older than this are dropped
COLUMNS = [
    "key",
    "source",
    "forecast_id",
    "content_sha12",
    "cyclone_name",
    "disturbance_id",
    "season",
    "base_time",
    "lat",
    "lon",
    "received_at",
    "deadline",
    "status",  # pending | sent | superseded | expired
]
TIME_COLS = ["base_time", "received_at", "deadline"]


def _prefix(test: bool) -> str:
    return f"{PROJECT_PREFIX}/monitoring/{'test_' if test else ''}"


def index_blob(test: bool) -> str:
    """Read by the Fiji poller too: keep the name and the columns."""
    return f"{_prefix(test)}info_pending.csv"


def context_blob(test: bool, key: str) -> str:
    return f"{_prefix(test)}info_context/{key}.json"


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=COLUMNS)
    for col in TIME_COLS:  # naive UTC, like the forecasts' times
        df[col] = pd.to_datetime(df[col], utc=True).dt.tz_localize(None)
    for col in ("lat", "lon"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    df["season"] = pd.to_numeric(df["season"]).astype("Int64")
    for col in ("disturbance_id", "cyclone_name"):
        df[col] = df[col].fillna("").astype(str)
    return df


def load_index(test: bool) -> pd.DataFrame:
    try:
        df = stratus.load_csv_from_blob(
            index_blob(test), stage="dev", dtype=str
        )
    except ResourceNotFoundError:
        df = pd.DataFrame(columns=COLUMNS)
    return _normalise(df)


def save_index(df: pd.DataFrame, test: bool) -> None:
    out = df.copy()
    for col in TIME_COLS:
        out[col] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    stratus.upload_csv_to_blob(out, index_blob(test), stage="dev")


def save_context(context: dict, test: bool, key: str) -> None:
    data = json.dumps(context).encode()
    stratus.upload_blob_data(data, context_blob(test, key), stage="dev")


def load_context(test: bool, key: str) -> dict:
    data = stratus.load_blob_data(context_blob(test, key), stage="dev")
    return json.loads(data)


def make_row(
    source: str,
    forecast,
    content_sha12: str,
    forecast_id: str,
    now: pd.Timestamp,
) -> dict:
    """A forecast's index row; ``forecast`` is a parsed Forecast."""
    first = forecast.track.sort_values("leadtime").iloc[0]
    return {
        "key": f"{forecast_id}_{content_sha12}",
        "source": source,
        "forecast_id": forecast_id,
        "content_sha12": content_sha12,
        "cyclone_name": str(forecast.cyclone_name or ""),
        "disturbance_id": str(forecast.disturbance_id or ""),
        "season": forecast.season,
        "base_time": pd.Timestamp(forecast.base_time),
        "lat": float(first["lat"]),
        "lon": float(first["lon"]),
        "received_at": now,
        "deadline": now + pd.Timedelta(hours=WAIT_HOURS),
        "status": "pending",
    }


def storm_name(name) -> str | None:
    """'TC LOLA', 'Lola', 'Severe Tropical Cyclone Lola' -> 'LOLA'; None
    for no name or a number ('08F')."""
    words = re.sub(r"[^A-Z]", " ", str(name or "").upper()).split()
    generic = {"TC", "STC", "TD", "SEVERE", "TROPICAL", "CYCLONE"}
    generic |= {"DEPRESSION", "DISTURBANCE", "NAN", "NONE", "UNNAMED"}
    words = [w for w in words if w not in generic]
    out = " ".join(words)
    return out if len(out) >= 3 else None


def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def same_storm_and_time(a, b) -> bool:
    gap = abs(pd.Timestamp(a["base_time"]) - pd.Timestamp(b["base_time"]))
    if gap > pd.Timedelta(hours=MATCH_HOURS):
        return False
    if a["disturbance_id"] and a["disturbance_id"] == b["disturbance_id"]:
        return True
    name = storm_name(a["cyclone_name"])
    if name and name == storm_name(b["cyclone_name"]):
        return True
    return distance_km(a["lat"], a["lon"], b["lat"], b["lon"]) <= MATCH_KM


def _due(index: pd.DataFrame, now: pd.Timestamp) -> pd.Series:
    return (index["status"] == "pending") & (index["deadline"] <= now)


def _partner(index: pd.DataFrame, key: str, taken: set) -> str | None:
    """The other provider's forecast for the same storm and time: one
    still waiting first, then the latest one already sent; the nearest if
    there are several (two storms at once)."""
    row = index[index["key"] == key].iloc[0]
    cands = index[
        (index["source"] != row["source"])
        & index["status"].isin(["pending", "sent"])
        & ~index["key"].isin(taken)
    ]
    if cands.empty:
        return None
    cands = cands[cands.apply(lambda r: same_storm_and_time(r, row), axis=1)]
    if cands.empty:
        return None
    cands = cands.assign(
        waiting=cands["status"] == "pending",
        km=cands.apply(
            lambda r: distance_km(r["lat"], r["lon"], row["lat"], row["lon"]),
            axis=1,
        ),
    ).sort_values(
        ["waiting", "km", "received_at"], ascending=[False, True, False]
    )
    return cands.iloc[0]["key"]


def _prune(index: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Drop old rows, but never one still waiting: those go out (or
    expire) first."""
    old = index["received_at"] < now - pd.Timedelta(days=KEEP_DAYS)
    return index[~old | (index["status"] == "pending")].reset_index(drop=True)


def plan_flush(
    index: pd.DataFrame,
    now: pd.Timestamp,
    new_key: str | None = None,
    max_age_hours: float | None = None,
):
    """The information emails that go out now: ``new_key``'s with its
    partner if it has one, and every row past its deadline, with its
    partner if one has arrived by now, else alone. A row past its deadline
    whose forecast is older than ``max_age_hours`` expires instead.

    Returns (index, actions, expired): an action is a tuple of index keys,
    one email each. Nothing is marked sent here: mark_sent after each
    email went out, so a failure leaves the rows waiting and the next run
    sends them again (the email record stops a repeat)."""
    index = _prune(index, now)
    actions, taken = [], set()
    if new_key is not None:
        partner = _partner(index, new_key, taken)
        if partner is not None:
            actions.append((new_key, partner))
            taken |= {new_key, partner}
    due = index[_due(index, now) & ~index["key"].isin(taken)]
    due = due.sort_values("deadline")
    expired = []
    for key, base in zip(due["key"], due["base_time"]):
        if key in taken:
            continue
        if max_age_hours is not None and now - base > pd.Timedelta(
            hours=max_age_hours
        ):
            expired.append(key)
            continue
        partner = _partner(index, key, taken)
        actions.append((key,) if partner is None else (key, partner))
        taken |= {key} | ({partner} if partner else set())
    index.loc[index["key"].isin(expired), "status"] = "expired"
    return index, actions, expired


def plan(
    index: pd.DataFrame,
    row: dict,
    now: pd.Timestamp,
    max_age_hours: float | None = None,
):
    """Add a forecast's row (waiting) and decide which information emails
    go out now: see plan_flush."""
    index = index.copy()
    same = index["key"] == row["key"]
    if same.any():  # the same CSV again (a retry): pair it if still waiting
        waiting = (index.loc[same, "status"] == "pending").any()
        return plan_flush(
            index, now, row["key"] if waiting else None, max_age_hours
        )
    matches = (
        index.apply(lambda r: same_storm_and_time(r, row), axis=1).astype(bool)
        if not index.empty
        else pd.Series(dtype=bool)
    )
    # a re-issue replaces an earlier forecast still waiting for its partner
    earlier = matches & (index["source"] == row["source"])
    index.loc[
        earlier & (index["status"] == "pending"), "status"
    ] = "superseded"
    index = _normalise(
        pd.concat([index, pd.DataFrame([dict(row)])], ignore_index=True)
    )
    return plan_flush(index, now, row["key"], max_age_hours)


def mark_sent(index: pd.DataFrame, keys) -> pd.DataFrame:
    index = index.copy()
    index.loc[index["key"].isin(list(keys)), "status"] = "sent"
    return index


def render_info(contexts: list, subject_prefix: str = "") -> dict:
    """The information email for one or two contexts (one per source):
    subject, body, attachments and its send-once record key."""
    from src.monitoring.content import render_template

    by = {c["source"]: c for c in contexts}
    vmhs, fms = by.get("vmhs"), by.get("fms")
    cyclone_name = (vmhs or fms)["cyclone_name"]
    met = []
    if fms and fms["readiness_met"]:
        met.append("READINESS")
    if vmhs and vmhs["action_met"]:
        met.append("ACTION")
    if vmhs and vmhs["observational_met"]:
        met.append("OBSERVATIONAL")
    if met:
        word = "TRIGGERS" if len(met) > 1 else "TRIGGER"
        activation = f"({' AND '.join(met)} {word} ACTIVATED)"
    elif vmhs and fms:
        activation = "(NOT ACTIVATED)"
    elif fms:
        activation = "(READINESS TRIGGER NOT ACTIVATED)"
    else:
        activation = "(ACTION AND OBSERVATIONAL TRIGGERS NOT ACTIVATED)"
    ordered = [c for c in (vmhs, fms) if c]
    return {
        "subject": (
            f"{subject_prefix}Anticipatory action Vanuatu: Cyclone "
            f"{cyclone_name} forecast information {activation}"
        ),
        "body": render_template(
            "informational.html",
            {"cyclone_name": cyclone_name, "vmhs": vmhs, "fms": fms},
        ),
        "media": list(vmhs.get("media", [])) if vmhs else [],
        "record_id": "+".join(c["forecast_id"] for c in ordered),
        "record_sha": "+".join(c["content_sha12"] for c in ordered),
    }
