import os
import re

import pandas as pd

from src.utils import blob

TEST_LIST = os.getenv("TEST_LIST")
if TEST_LIST == "False":
    TEST_LIST = False
else:
    TEST_LIST = True

EMAIL_HOST = os.getenv("CHD_DS_HOST")
EMAIL_PORT = int(os.getenv("CHD_DS_PORT"))
EMAIL_PASSWORD = os.getenv("CHD_DS_EMAIL_PASSWORD")
EMAIL_USERNAME = os.getenv("CHD_DS_EMAIL_USERNAME")
EMAIL_ADDRESS = os.getenv("CHD_DS_EMAIL_ADDRESS")
INFO_ALWAYS_TO = os.getenv("INFO_ALWAYS_TO")


def is_valid_email(email):
    # Define a regex pattern for validating an email
    email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"

    # Use the re.match() method to check if the email matches the pattern
    if re.match(email_regex, email):
        return True
    else:
        return False


def get_distribution_list() -> pd.DataFrame:
    """Load distribution list from blob storage."""
    if TEST_LIST:
        blob_name = f"{blob.PROJECT_PREFIX}/email/test_distribution_list.csv"
    else:
        blob_name = f"{blob.PROJECT_PREFIX}/email/distribution_list.csv"
    df = blob.load_csv_from_blob(blob_name)
    df["name"] = df["name"].fillna("").astype(str)
    return df


def segment_emails(
    to_list: pd.DataFrame,
    cc_list: pd.DataFrame,
    recipient_limit: int = 50,
    default_to: str = EMAIL_ADDRESS,
):
    """Segments TO and CC mailing lists if they exceed the limit.
    Prioritizes sending first to TO list, then fills in remaining spots with
    CC list. If all TO list fits in the first email, the second email will use
    the default_to as TO, since TO cannot be blank.
    Only works for combined list lengths up to two times the limit (i.e. will
    combine into maximum two emails).
    """
    to_list_chunks, cc_list_chunks = [], []
    default_to_df = pd.DataFrame(
        [
            {
                "email": default_to,
                "name": "OCHA Centre for Humanitarian Data",
            }
        ]
    )
    if len(to_list) + len(cc_list) > recipient_limit:
        print(f"Over 50 recipients: {len(to_list)=}; {len(cc_list)=}")
        if len(to_list) > recipient_limit:
            to_list_chunks.append(to_list.iloc[:recipient_limit])
            to_list_chunks.append(to_list.iloc[recipient_limit:])
            cc_list_chunks.append(pd.DataFrame(columns=["email", "name"]))
            cc_list_chunks.append(cc_list)
        else:
            to_list_chunks.append(to_list)
            to_list_chunks.append(default_to_df)
            new_lim = recipient_limit - len(to_list)
            cc_list_chunks.append(cc_list.iloc[:new_lim])
            cc_list_chunks.append(cc_list.iloc[new_lim:])
    else:
        to_list_chunks.append(to_list)
        cc_list_chunks.append(cc_list)
    return to_list_chunks, cc_list_chunks
