"""Vanuatu tropical cyclone trigger monitor: one forecast CSV in, emails out.

Modelled on pa-aa-fji-storms (pipelines/monitor_forecast_trigger.py), with
the Vanuatu framework's three triggers. All count people exposed to winds of
at least 64 knots, threshold 15,000:

- ``--source fms`` (RSMC Nadi): the readiness trigger, on the part of the
  track 72 to 120 hours ahead, in Sanma, Shefa and Tafea. FMS forecasts
  cover the whole South Pacific: a forecast whose track (to 120 h) never
  comes within 1,000 km of Vanuatu is not emailed about.
- ``--source vmhs`` (Vanuatu Met): the action trigger, on the part of the
  track 24 to 72 hours ahead, in the same three provinces; and the
  observational trigger, on the observed track (the analysis position of
  every VMHS forecast for the storm, see src/monitoring/observed.py), in all
  of Vanuatu.

Each forecast gets an information email; a met trigger also gets its
activation email. Every email goes out at most once per forecast CSV
(src/monitoring/email_record.py).

Switches (env): TEST_EMAIL (default True: test list only), DRY_RUN (default
True: nothing sent or recorded), SIMULATE_TRIGGER (default False: without a
CSV, run the made-up forecast in src/monitoring/data/),
MAX_FORECAST_AGE_HOURS (no real-list emails about an older forecast).
"""

import argparse
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import ocha_stratus as stratus
import pandas as pd
from dotenv import load_dotenv

from src.constants import (
    ADM1_AOI_PCODES,
    EXP_THRESHOLD_64_KNOTS,
    FJI_CRS,
    LOCAL_TIMEZONE,
)
from src.datasources import codab
from src.monitoring import email_record, observed
from src.monitoring.content import render_template
from src.monitoring.exposure import ExposureContext
from src.monitoring.forecast import (
    OFFICIAL_TRACK,
    VUT_AEQD,
    interpolated,
    min_distance_km,
    parse_forecast_csv,
    safe_name,
    segment,
)
from src.monitoring.listmonk import (
    PROD_INFO_LIST_IDS,
    PROD_TRIGGER_LIST_IDS,
    TEST_LIST_IDS,
    create_and_send_campaign,
    upload_file,
)
from src.monitoring.pipeline_utils import get_logger, load_boolean_env
from src.monitoring.plotting import (
    build_circle_template,
    fig_to_base64,
    plot_bubbles_and_swaths,
    plot_thermometer,
)
from src.utils.wind_buffers import METRIC_CRS, wind_buffers_from_track

load_dotenv()

logger = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "monitoring" / "data"
HISTORICAL_EXPOSURE = DATA_DIR / "historical_exposure.csv"
# made up, see exploration/make_monitoring_data.py
SIMULATED_FORECAST = DATA_DIR / "simulated_forecast.csv"

INFO_DISTANCE_KM = 1000
SPEEDS = (34, 50, 64)
# lead-time windows (hours after the forecast was produced)
WINDOWS = {"fms": (72, 120), "vmhs": (24, 72)}
WINDOW_STR = {"fms": "72 to 120 hours", "vmhs": "24 to 72 hours"}
SOURCE_NAME = {"fms": "RSMC Nadi", "vmhs": "VMHS"}
SHIFT_DEG_STEP = 10
# bubble size (m2 per person, equal-area); Fiji uses 80,000 over a country
# half as long with twice the people
BUBBLE_AREA_PER_PERSON = 200_000

TEST_EMAIL = load_boolean_env("TEST_EMAIL", True)
SIMULATE_TRIGGER = load_boolean_env("SIMULATE_TRIGGER", False)
DRY_RUN = load_boolean_env("DRY_RUN", True)
MAX_FORECAST_AGE_HOURS = os.getenv("MAX_FORECAST_AGE_HOURS", "")

INFO_LIST_IDS = TEST_LIST_IDS if TEST_EMAIL else PROD_INFO_LIST_IDS
TRIGGER_LIST_IDS = TEST_LIST_IDS if TEST_EMAIL else PROD_TRIGGER_LIST_IDS
# Test and simulated sends are labelled and kept out of the real record.
IS_TEST_SEND = TEST_EMAIL or SIMULATE_TRIGGER
SUBJECT_PREFIX = "[TEST] " if IS_TEST_SEND else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["fms", "vmhs"], required=True)
    parser.add_argument("--csv-blob", default="", help="dev-blob CSV name")
    parser.add_argument("--csv-file", default="", help="local CSV path")
    return parser.parse_args()


def vut_time_str(dt: pd.Timestamp) -> str:
    """UTC (naive) -> 'YYYY-MM-DD HH:MM (Vanuatu time)'."""
    local = (
        pd.Timestamp(dt)
        .tz_localize("UTC")
        .tz_convert(ZoneInfo(LOCAL_TIMEZONE))
    )
    return f"{local:%Y-%m-%d %H:%M} (Vanuatu time)"


def send_once(
    record, email_type: str, forecast_id: str, content_sha12: str, **campaign
):
    """Send unless this CSV's email of this type already went out."""
    if email_record.already_sent(
        record, forecast_id, content_sha12, email_type
    ):
        logger.info(
            f"{email_type} email for {forecast_id} ({content_sha12}) "
            "already sent; skipping."
        )
        return record
    campaign_id = create_and_send_campaign(**campaign)
    logger.info(f"Sent {email_type} email (Listmonk campaign {campaign_id}).")
    return email_record.add_sent(
        record,
        IS_TEST_SEND,
        forecast_id,
        content_sha12,
        email_type,
        campaign_id,
    )


def swaths(track: pd.DataFrame) -> dict:
    """{speed: swath geometry in EPSG:3832 (or None)}."""
    out = {s: None for s in SPEEDS}
    if track.empty:
        return out
    # tracks come interpolated (src/monitoring/forecast.py)
    bufs = wind_buffers_from_track(track, speeds=SPEEDS, interpolate=False)
    for speed, geom in zip(bufs["buffer_speed"], bufs.geometry):
        out[int(speed)] = geom
    return out


def shift_track(track: pd.DataFrame, azimuth_deg: float) -> pd.DataFrame:
    """Every point moved by its uncertainty radius towards ``azimuth_deg``
    (true distances, around Vanuatu)."""
    pts = gpd.GeoSeries(
        gpd.points_from_xy(track["lon"], track["lat"]), crs=FJI_CRS
    ).to_crs(VUT_AEQD)
    dist = track["uncertainty_nm"].fillna(0).to_numpy() * 1852.0
    angle = np.deg2rad(azimuth_deg)
    moved = gpd.GeoSeries(
        gpd.points_from_xy(
            pts.x + dist * np.sin(angle), pts.y + dist * np.cos(angle)
        ),
        crs=VUT_AEQD,
    ).to_crs(FJI_CRS)
    out = track.copy()
    out["lon"] = moved.x.to_numpy() % 360
    out["lat"] = moved.y.to_numpy()
    out["shift_deg"] = azimuth_deg
    return out


def uncertainty_cone(track: pd.DataFrame):
    """Union of the uncertainty circles along the track (for the plot)."""
    if track.empty:
        return None
    pts = gpd.GeoSeries(
        gpd.points_from_xy(track["lon"], track["lat"]), crs=FJI_CRS
    ).to_crs(METRIC_CRS)
    radii = track["uncertainty_nm"].fillna(0).to_numpy() * 1852.0
    circles = [p.buffer(max(r, 1.0)) for p, r in zip(pts, radii)]
    cone = gpd.GeoSeries(circles, crs=METRIC_CRS).union_all()
    return gpd.GeoSeries([cone], crs=METRIC_CRS).to_crs(FJI_CRS).iloc[0]


def swath_gdf(geoms: dict) -> gpd.GeoDataFrame:
    rows = [(s, g) for s, g in geoms.items() if g is not None]
    return gpd.GeoDataFrame(
        {"buffer_speed": [s for s, _ in rows]},
        geometry=[g for _, g in rows],
        crs=METRIC_CRS,
    ).to_crs(FJI_CRS)


def load_historical_stats() -> pd.DataFrame:
    """Observed 64 kt exposure in Sanma, Shefa and Tafea per historical
    storm (exploration/make_monitoring_data.py)."""
    return pd.read_csv(HISTORICAL_EXPOSURE)


def area_exposure(
    ctx: ExposureContext, geoms: dict, level: int = 2
) -> pd.DataFrame:
    """Long table: ADM{level}_PCODE, buffer_speed, pop_exposed."""
    frames = []
    for speed, geom in geoms.items():
        s = ctx.by_area(geom, level)
        frames.append(
            pd.DataFrame(
                {
                    f"ADM{level}_PCODE": s.index,
                    "buffer_speed": speed,
                    "pop_exposed": s.to_numpy(),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def load_csv(args) -> bytes:
    if args.csv_blob:
        logger.info(f"Loading the forecast CSV {args.csv_blob}.")
        return stratus.load_blob_data(args.csv_blob, stage="dev")
    if args.csv_file:
        return Path(args.csv_file).read_bytes()
    if SIMULATE_TRIGGER:
        logger.info("SIMULATE_TRIGGER: using the simulated forecast.")
        return SIMULATED_FORECAST.read_bytes()
    raise SystemExit("No forecast CSV given (--csv-blob or --csv-file).")


def main() -> None:
    logger.info("Vanuatu monitor forecast trigger pipeline started.")
    logger.info(
        f"Env vars set to: {TEST_EMAIL=}, {SIMULATE_TRIGGER=}, {DRY_RUN=}"
    )
    if (
        SIMULATE_TRIGGER
        and not TEST_EMAIL
        and not load_boolean_env("ALLOW_REAL_SIMULATION", False)
    ):
        raise SystemExit(
            "SIMULATE_TRIGGER=True with TEST_EMAIL=False would send a "
            "simulated forecast to the real lists. Set "
            "ALLOW_REAL_SIMULATION=True if that is really what you want."
        )
    args = parse_args()
    source = args.source
    csv_bytes = load_csv(args)
    content_sha12 = hashlib.sha256(csv_bytes).hexdigest()[:12]

    fc = parse_forecast_csv(csv_bytes)
    cyclone_name = fc.cyclone_name
    forecast_display_str = vut_time_str(fc.base_time)
    forecast_id = (
        f"{source}_{safe_name(cyclone_name)}_{fc.season}_fcast_"
        f"{fc.base_time:%Y%m%dT%H%MZ}"
    )
    logger.info(
        f"{source.upper()} forecast {forecast_id} ({content_sha12}): "
        f"issued {fc.base_time}, {len(fc.track)} points to "
        f"{fc.track['leadtime'].max():.0f} h"
    )

    adm2 = codab.load_codab_from_blob(admin_level=2)
    official = fc.track_type == OFFICIAL_TRACK

    # The observed track (src/monitoring/observed.py), one per source: for
    # VMHS it is the observational trigger, for FMS it is only drawn on the
    # maps. Recorded before the distance check, so a storm's track is
    # complete by the time it comes near. It must never stop the emails: a
    # problem is logged, the emails go out, the run fails at the end.
    observed_error = None
    obs_track = None
    if official:
        try:
            points = observed.load_points(IS_TEST_SEND, source)
            points = observed.add_point(
                points, observed.analysis_point(fc, content_sha12)
            )
            if not DRY_RUN:
                observed.save_points(points, IS_TEST_SEND, source)
            obs_track = observed.observed_track(
                points, fc.disturbance_id, fc.season, until=fc.base_time
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Observed track failed")
            observed_error = exc

    if source == "fms":
        distance = min_distance_km(
            fc.track, adm2, max_leadtime=WINDOWS["fms"][1]
        )
        logger.info(f"Closest approach to Vanuatu: {distance:,.0f} km")
        if distance > INFO_DISTANCE_KM:
            logger.info(
                f"More than {INFO_DISTANCE_KM:,} km from Vanuatu: no "
                "Vanuatu emails for this forecast."
            )
            if observed_error is not None:
                raise RuntimeError(
                    "The observed track failed (see above)."
                ) from observed_error
            return

    # after the distance check: a non-Official FMS track for a far-away
    # storm ends quietly instead of failing the run
    if not official:
        raise SystemExit(
            f"Track type {fc.track_type!r}, not {OFFICIAL_TRACK!r}: "
            "not monitored."
        )

    if MAX_FORECAST_AGE_HOURS and not IS_TEST_SEND and not DRY_RUN:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age_hours = (now - fc.base_time).total_seconds() / 3600
        if age_hours > float(MAX_FORECAST_AGE_HOURS):
            raise SystemExit(
                f"Forecast issued {fc.base_time} is {age_hours:.0f} h old "
                f"(limit {MAX_FORECAST_AGE_HOURS} h): not emailing the "
                "real lists about it."
            )

    ctx = ExposureContext(adm2=adm2)
    lo, hi = WINDOWS[source]
    max_lt = fc.track["leadtime"].max()
    # a forecast that ends exactly at the window's start is evaluated on
    # that one point (the window is inclusive)
    window_covered = max_lt >= lo
    seg = segment(fc.track, lo, hi)
    geoms = swaths(seg)
    exp_aoi = {s: ctx.aoi(g) for s, g in geoms.items()}
    trigger_exp = exp_aoi[64]
    triggered = trigger_exp >= EXP_THRESHOLD_64_KNOTS
    logger.info(
        f"{WINDOW_STR[source]} exposure in Sanma/Shefa/Tafea: "
        + ", ".join(f"{s} kt {v:,}" for s, v in exp_aoi.items())
    )

    early_exp = None
    if source == "fms":
        early = swaths(segment(fc.track, 0, lo))[64]
        early_exp = ctx.aoi(early)
        logger.info(f"0 to {lo} h 64 kt exposure (info only): {early_exp:,}")

    observational_exp = None
    observational = False
    n_observed = 0
    observed_display_str = ""
    obs_geoms = {s: None for s in SPEEDS}
    if obs_track is not None and not obs_track.empty:
        try:
            n_observed = len(obs_track)
            observed_display_str = vut_time_str(obs_track["valid_time"].max())
            obs_geoms = swaths(interpolated(obs_track))
            obs_national = ctx.national(obs_geoms[64])
            logger.info(
                f"Observed track ({n_observed} points) 64 kt exposure, all "
                f"of Vanuatu: {obs_national:,}"
            )
            if source == "vmhs":
                observational_exp = obs_national
                observational = observational_exp >= EXP_THRESHOLD_64_KNOTS
        except Exception as exc:  # noqa: BLE001
            logger.exception("Observed track failed; observational not met")
            observed_error = exc
            observational_exp, observational = None, False
            obs_geoms = {s: None for s in SPEEDS}

    email_base_name = ("[TEST]_" if IS_TEST_SEND else "") + forecast_id
    sent_record = None
    if not DRY_RUN:
        sent_record = email_record.load_record(IS_TEST_SEND)
        # Prove the record is writable before anything is sent, so a
        # failed write after a send can't lead to a repeat on retry.
        email_record.save_record(sent_record, IS_TEST_SEND)

        # (email type, record key, record sha): readiness and action go out
        # for every forecast that meets them, as in Fiji. The observed track
        # only grows, so the observational email goes out once per storm
        # (disturbance id: it stays when a storm is named), not with every
        # VMHS forecast after the threshold is crossed.
        trigger_emails = []
        if source == "fms" and triggered:
            trigger_emails.append(("readiness", forecast_id, content_sha12))
        if source == "vmhs" and triggered:
            trigger_emails.append(("action", forecast_id, content_sha12))
        if source == "vmhs" and observational:
            storm_id = f"vmhs_{fc.disturbance_id}_{fc.season}_observed"
            trigger_emails.append(("observational", storm_id, "storm"))
        for email_type, record_id, record_sha in trigger_emails:
            logger.info(f"{email_type} trigger met; sending email.")
            sent_record = send_once(
                sent_record,
                email_type,
                record_id,
                record_sha,
                subject=(
                    f"{SUBJECT_PREFIX}Anticipatory action Vanuatu: Cyclone "
                    f"{cyclone_name} {email_type} trigger ACTIVATED"
                ),
                name=f"{email_base_name}_{email_type}",
                list_ids=TRIGGER_LIST_IDS,
                body=render_template(
                    f"{email_type}.html",
                    {
                        "cyclone_name": cyclone_name,
                        "forecast_display_str": forecast_display_str,
                        "observed_display_str": observed_display_str,
                    },
                ),
            )

    # Perturbed tracks: the likely range around the most likely value
    rows = []
    shifted = {}
    for deg in range(0, 360, SHIFT_DEG_STEP):
        st = segment(shift_track(fc.track, deg), lo, hi)
        g = swaths(st)
        shifted[deg] = (st, g)
        rows.append(
            {
                "shift_deg": deg,
                **{f"exp_{s}": ctx.aoi(g[s]) for s in SPEEDS},
                "nat_34": ctx.national(g[34]),
            }
        )
    df_shift = pd.DataFrame(rows).sort_values(
        [f"exp_{s}" for s in (64, 50, 34)], ascending=False
    )
    worst_row = df_shift.iloc[0].copy()
    best_row = df_shift.iloc[-1].copy()
    if (best_row[[f"exp_{s}" for s in SPEEDS]] == 0).all():
        opposite = (worst_row["shift_deg"] + 180) % 360
        best_row = df_shift.loc[
            (df_shift["shift_deg"] - opposite).abs().idxmin()
        ].copy()
    logger.info(f"Best case:\n{best_row.to_frame().T.to_string(index=False)}")
    logger.info(
        f"Worst case:\n{worst_row.to_frame().T.to_string(index=False)}"
    )
    # the thermometer is about the three provinces; the area-council CSV and
    # PDF cover all of Vanuatu, so they're gated on national exposure
    any_64 = worst_row["exp_64"] > 0 or trigger_exp > 0
    any_34 = df_shift["nat_34"].max() > 0 or ctx.national(geoms[34]) > 0

    img_base64_thermometer = None
    if any_64:
        fig, _ = plot_thermometer(
            main_value=trigger_exp,
            low_bound=best_row["exp_64"],
            high_bound=worst_row["exp_64"],
            df_stats=load_historical_stats(),
            trigger_threshold=EXP_THRESHOLD_64_KNOTS,
            cyclone_name=cyclone_name,
            forecast_display_str=forecast_display_str,
            title=f"64 knot exposure, {WINDOW_STR[source]}",
        )
        img_base64_thermometer = fig_to_base64(fig)

    adm2_exp_id = bubbles_plot_id = None
    if any_34:
        logger.info("Calculating province and area council exposure.")
        _, worst_geoms = shifted[int(worst_row["shift_deg"])]
        _, best_geoms = shifted[int(best_row["shift_deg"])]
        df_ml = area_exposure(ctx, geoms, level=2)
        df_out = df_ml.pivot(
            index="ADM2_PCODE", columns="buffer_speed", values="pop_exposed"
        )
        df_out.columns = [f"exp_{s}_knot" for s in df_out.columns]
        df_out = (
            ctx.adm2[["ADM1_PCODE", "ADM1_EN", "ADM2_PCODE", "ADM2_EN"]]
            .merge(df_out.reset_index())
            .assign(
                in_trigger_area=lambda d: d["ADM1_PCODE"].isin(ADM1_AOI_PCODES)
            )
            .sort_values(
                [f"exp_{s}_knot" for s in (64, 50, 34)], ascending=False
            )
        )
        os.makedirs("temp", exist_ok=True)
        adm2_csv = f"temp/{forecast_id}_adm2_exposure.csv"
        df_out.to_csv(adm2_csv, index=False)

        # The plot is by province: 66 area-council bubbles were unreadable
        # (the CSV above keeps the area councils).
        df_exp = pd.concat(
            [
                area_exposure(ctx, geoms, 1).assign(limit="middle"),
                area_exposure(ctx, worst_geoms, 1).assign(limit="worst"),
                area_exposure(ctx, best_geoms, 1).assign(limit="best"),
            ],
            ignore_index=True,
        )
        gdf_adm = (
            ctx.adm2.dissolve(by=["ADM1_PCODE", "ADM1_EN"])
            .reset_index()[["ADM1_PCODE", "ADM1_EN", "geometry"]]
            .merge(ctx.population(1).rename_axis("ADM1_PCODE").reset_index())
        )
        template = build_circle_template(
            gdf_adm,
            id_col="ADM1_PCODE",
            pop_col="pop_total",
            area_per_person=BUBBLE_AREA_PER_PERSON,
        )

        def _pts(track):
            return gpd.GeoDataFrame(
                track,
                geometry=gpd.points_from_xy(track["lon"], track["lat"]),
                crs=FJI_CRS,
            )

        _, _, pdf_path = plot_bubbles_and_swaths(
            gdf_mostlikely_buffers=swath_gdf(geoms),
            gdf_worst_buffers=swath_gdf(worst_geoms),
            gdf_best_buffers=swath_gdf(best_geoms),
            gdf_adm_swath_plot=gdf_adm,
            df_adm_template=template,
            gdf_adm=gdf_adm,
            df_exp_adm=df_exp,
            cyclone_name=cyclone_name,
            forecast_display_str=forecast_display_str,
            forecast_id=forecast_id,
            save_local=True,
            # the whole forecast track (6-hourly points); the swaths and
            # the cone are the trigger window only
            gdf_tracks_plot=[
                _pts(fc.track),
                _pts(shift_track(fc.track, worst_row["shift_deg"])),
                _pts(shift_track(fc.track, best_row["shift_deg"])),
            ],
            uncertainty_cone=uncertainty_cone(seg),
            gdf_observed_buffers=(
                swath_gdf(obs_geoms)
                if any(g is not None for g in obs_geoms.values())
                else None
            ),
            gdf_observed_track=(
                _pts(obs_track)
                if obs_track is not None and not obs_track.empty
                else None
            ),
            swath_note=(
                f"Wind swaths: the {WINDOW_STR[source]} part of the "
                f"forecast ({SOURCE_NAME[source]}), and the swath observed so far"
                if n_observed
                else f"Wind swaths: the {WINDOW_STR[source]} part of the "
                f"forecast ({SOURCE_NAME[source]})"
            ),
            id_col="ADM1_PCODE",
            name_col="ADM1_EN",
            file_tag="adm1",
            label_max_len=12,
            label_font=(10, 22),
        )
        if not DRY_RUN:
            adm2_exp_id = upload_file(adm2_csv)["id"]
            bubbles_plot_id = upload_file(pdf_path)["id"]
        else:
            logger.info(f"DRY RUN: wrote {adm2_csv} and {pdf_path}.")

    def status(met: bool) -> str:
        return "ACTIVATED" if met else "NOT ACTIVATED"

    def exp_str(value, covered=True) -> str:
        if not covered:
            return f"n/a (the forecast only goes to {max_lt:.0f} hours)"
        return f"{value:,.0f}"

    met = []
    if source == "fms" and triggered:
        met.append("READINESS")
    if source == "vmhs" and triggered:
        met.append("ACTION")
    if observational:
        met.append("OBSERVATIONAL")
    if met:
        trigger_word = "TRIGGERS" if len(met) > 1 else "TRIGGER"
        activation_subject = f"({' AND '.join(met)} {trigger_word} ACTIVATED)"
    else:
        activation_subject = "(NOT ACTIVATED)"

    body = render_template(
        "informational.html",
        {
            "source": source,
            "cyclone_name": cyclone_name,
            "forecast_display_str": forecast_display_str,
            "readiness_str": status(triggered),
            "action_str": status(triggered),
            "observational_str": status(observational),
            "readiness_exp": exp_str(trigger_exp, window_covered),
            "early_exp": exp_str(early_exp) if early_exp is not None else "",
            "action_exp": exp_str(trigger_exp, window_covered),
            "observational_exp": (
                exp_str(observational_exp)
                if observational_exp is not None
                else ""
            ),
            "n_observed": n_observed,
            "observed_display_str": observed_display_str,
            "window_str": WINDOW_STR[source],
            "source_name": SOURCE_NAME[source],
            "img_base64_thermometer": img_base64_thermometer,
            "forecast_id": forecast_id,
            "any_64_knot_exposure": any_64,
            "any_34_knot_exposure": any_34,
        },
    )
    subject = (
        f"{SUBJECT_PREFIX}Anticipatory action Vanuatu: Cyclone "
        f"{cyclone_name} forecast information {activation_subject}"
    )
    if DRY_RUN:
        os.makedirs("temp", exist_ok=True)
        Path(f"temp/{forecast_id}_info.html").write_text(body)
        logger.info(f"DRY RUN: not sending {subject!r}")
    else:
        logger.info("Sending info email.")
        send_once(
            sent_record,
            "forecast_info",
            forecast_id,
            content_sha12,
            subject=subject,
            name=f"{email_base_name}_forecast_info",
            list_ids=INFO_LIST_IDS,
            body=body,
            media=[m for m in (adm2_exp_id, bubbles_plot_id) if m],
        )
    if observed_error is not None:
        raise RuntimeError(
            "The observed track failed (see above); the other emails went "
            "out, the observational trigger was not evaluated."
        ) from observed_error
    logger.info("Vanuatu monitor forecast trigger pipeline completed.")


if __name__ == "__main__":
    main()
