from io import StringIO

import pandas as pd
import requests

from src.utils import blob

ENSO_URL = (
    "https://origin.cpc.ncep.noaa.gov/products/analysis_monitoring/"
    "ensostuff/detrend.nino34.ascii.txt"
)


def process_enso():
    response = requests.get(ENSO_URL)
    response.raise_for_status()

    data = StringIO(response.text)

    df = pd.read_csv(data, sep=r"\s+")

    def anom_to_phase(anom):
        if anom >= 0.5:
            return "elnino"
        elif anom <= -0.5:
            return "lanina"
        else:
            return "neutral"

    def label_longterm_phase(group, phase_name):
        # Identify sequences with at least 5 consecutive identical phase names
        count = 0
        for i in range(len(group)):
            if group[i] == phase_name:
                count += 1
                if count >= 5:
                    df.loc[i - count + 1 : i, "phase_longterm"] = phase_name
            else:
                count = 0

    df["date"] = df["YR"].astype(str) + "-" + df["MON"].astype(str) + "-01"
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["ANOM_trimester"] = df["ANOM"].rolling(window=3).mean().shift(-2)
    df["ANOM_trimester_round"] = df["ANOM_trimester"].round(1)
    df["phase_trimester"] = df["ANOM_trimester_round"].apply(anom_to_phase)
    df["phase_longterm"] = "neutral"

    label_longterm_phase(df["phase_trimester"], "elnino")
    label_longterm_phase(df["phase_trimester"], "lanina")

    blob_name = f"{blob.PROJECT_PREFIX}/processed/enso/enso.parquet"
    blob.upload_parquet_to_blob(blob_name, df, stage="dev")


def load_enso():
    blob_name = f"{blob.PROJECT_PREFIX}/processed/enso/enso.parquet"
    return blob.load_parquet_from_blob(blob_name, stage="dev")
