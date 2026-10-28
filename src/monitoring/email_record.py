"""Record of the monitoring emails already sent, in the dev blob.

Each forecast CSV still gets its emails (info, plus readiness, action or
observational when triggered), but at most once per (forecast_id, CSV
content, email_type): a retried or re-triggered run for the same CSV skips
what already went out, while a corrected re-export of the same issuance
(different bytes) is emailed again. Copied from pa-aa-fji-storms
(src/email_record.py).

Test sends (TEST_EMAIL or SIMULATE_TRIGGER) use a separate record, so a
test can never mark a real forecast as sent. Under DRY_RUN nothing is sent,
so nothing is recorded.
"""

from datetime import datetime, timezone

import ocha_stratus as stratus
import pandas as pd
from azure.core.exceptions import ResourceNotFoundError

from src.constants import PROJECT_PREFIX

RECORD_BLOB = f"{PROJECT_PREFIX}/monitoring/email_record.csv"
TEST_RECORD_BLOB = f"{PROJECT_PREFIX}/monitoring/test_email_record.csv"
COLUMNS = [
    "forecast_id",
    "content_sha12",
    "email_type",
    "campaign_id",
    "sent_at_utc",
]


def record_blob(test: bool) -> str:
    return TEST_RECORD_BLOB if test else RECORD_BLOB


def load_record(test: bool) -> pd.DataFrame:
    try:
        df = stratus.load_csv_from_blob(
            record_blob(test), stage="dev", dtype=str
        )
    except ResourceNotFoundError:
        return pd.DataFrame(columns=COLUMNS)
    return df.reindex(columns=COLUMNS).fillna("")


def save_record(record: pd.DataFrame, test: bool) -> None:
    stratus.upload_csv_to_blob(record, record_blob(test), stage="dev")


def already_sent(
    record: pd.DataFrame, forecast_id: str, content_sha12: str, email_type: str
) -> bool:
    return bool(
        (
            (record["forecast_id"] == forecast_id)
            & (record["content_sha12"] == content_sha12)
            & (record["email_type"] == email_type)
        ).any()
    )


def add_sent(
    record: pd.DataFrame,
    test: bool,
    forecast_id: str,
    content_sha12: str,
    email_type: str,
    campaign_id: int,
) -> pd.DataFrame:
    """Append one sent email and save the record straight away."""
    row = {
        "forecast_id": forecast_id,
        "content_sha12": content_sha12,
        "email_type": email_type,
        "campaign_id": str(campaign_id),
        "sent_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    record = pd.concat([record, pd.DataFrame([row])], ignore_index=True)
    save_record(record, test)
    return record
