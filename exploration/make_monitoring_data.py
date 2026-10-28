"""Bake the historical exposure table the monitoring emails plot.

Reads docs/forecast-check/data/hist.json (observed exposure per storm, in
Sanma, Shefa and Tafea, written by exploration/make_trigger_data.py) and
writes src/monitoring/data/historical_exposure.csv for the thermometer plot
in pipelines/monitor_forecast_trigger.py. Re-run after make_trigger_data.py.

    uv run python exploration/make_monitoring_data.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

HIST = Path("docs/forecast-check/data/hist.json")
OUT = Path("src/monitoring/data/historical_exposure.csv")

storms = json.loads(HIST.read_text())["storms"]
df = pd.DataFrame(storms)
df["name_season"] = (
    df["name"].str.capitalize() + " " + df["season"].astype(str)
)
df = df.rename(
    columns={"exp34": "exp_34", "exp50": "exp_50", "exp64": "exp_64"}
)
df = df[["sid", "name_season", "exp_34", "exp_50", "exp_64", "cerf"]]
OUT.parent.mkdir(parents=True, exist_ok=True)
df.sort_values("exp_64", ascending=False).to_csv(OUT, index=False)
print(f"wrote {OUT} ({len(df)} storms)")


# --- simulated forecast ---------------------------------------------------
# A made-up TC Module CSV (no real forecast in it) for SIMULATE_TRIGGER and
# the tests: a Category 4 crossing Santo at ~48 h, Efate at ~78 h and Tanna
# at ~102 h, so it reaches both the action (24-72 h) and readiness
# (72-120 h) thresholds.
SIM_OUT = Path("src/monitoring/data/simulated_forecast.csv")
COLUMNS = (
    "Latitude,Longitude,Symbol,Category,Pressure,PressureOCI,RadiusOCI,"
    "Radius1000hPa,RadiusMaxWinds,MeanWind,WindGust,VerticalExtent,"
    "Uncertainty,FinalT,CurrentIntensity,GaleRadius,StrongGaleRadius,"
    "StormRadius,HurricaneRadius,NEGaleRadius,SEGaleRadius,SWGaleRadius,"
    "NWGaleRadius,NEStrongGaleRadius,SEStrongGaleRadius,SWStrongGaleRadius,"
    "NWStrongGaleRadius,NEStormRadius,SEStormRadius,SWStormRadius,"
    "NWStormRadius,NEHurricaneRadius,SEHurricaneRadius,SWHurricaneRadius,"
    "NWHurricaneRadius"
).split(",")
WAYPOINTS = [  # (lead time h, lat, lon)
    (0, -11.5, 166.0),
    (48, -15.3, 166.9),
    (78, -17.7, 168.3),
    (102, -19.5, 169.3),
    (120, -21.0, 170.0),
]
base = pd.Timestamp("2026-02-01T00:00:00Z")
lead = list(range(0, 121, 6))
lt, la, lo = zip(*WAYPOINTS)
lats = pd.Series(lead, dtype=float).apply(
    lambda h: float(np.interp(h, lt, la))
)
lons = pd.Series(lead, dtype=float).apply(
    lambda h: float(np.interp(h, lt, lo))
)
rows = []
for h, lat, lon in zip(lead, lats, lons):
    vals = dict.fromkeys(COLUMNS, "NaN")
    vals.update(
        Latitude=f"{lat:.2f}",
        Longitude=f"{lon:.2f}",
        Category="4.0",
        MeanWind="95.0",
        Uncertainty=f"{20 + 1.1 * h:.1f}",
        GaleRadius="100.0",
        StormRadius="50.0",
        HurricaneRadius="30.0",
    )
    t = base + pd.Timedelta(hours=h)
    rows.append(
        f"{t:%Y-%m-%dT%H:%M:%SZ}," + ",".join(vals[c] for c in COLUMNS)
    )
SIM_OUT.write_text(
    "\n".join(
        [
            f"# trackType=Official Forecast Track,baseTime={base:%Y-%m-%dT%H:%M:%SZ}",
            "# DisturbanceId=2526-99F",
            "# CycloneName=SIMULATED",
            "# MathType of Data",
            "(Time->(" + ",".join(COLUMNS) + "))",
            "# Column Headings",
            "Time[fmt=yyyy-MM-dd'T'HH:mm:ss'Z']," + ",".join(COLUMNS),
            "# Start of data",
            *rows,
        ]
    )
    + "\n"
)
print(f"wrote {SIM_OUT}")
