import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid

import pandas as pd
from html2text import html2text
from jinja2 import Environment, FileSystemLoader

from src.constants import (
    INFO_EMAIL_DISTANCE_THRESHOLD,
    OUTPUT_DIR,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from src.datasources.fms import str_from_report
from src.email.email_utils import (
    EMAIL_ADDRESS,
    EMAIL_HOST,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_USERNAME,
    INFO_ALWAYS_TO,
    get_distribution_list,
    segment_emails,
)


def send_info_email(
    report: dict,
    min_distance: float = 0,
    min_hours: float = 0,
    suppress_send: bool = False,
    save: bool = True,
    test_email: bool = False,
):
    """Sends email with info about forecast

    Parameters
    ----------
    report: dict
        Dict of forecast report
    min_distance: float = 0
        If min_distance is above INFO_EMAIL_DISTANCE_THRESHOLD,
        email only sends to INFO_ALWAYS_TO
    min_hours: float = 0
        leadtime to closest pass
    suppress_send: bool = False
        If True, does not actually send email
    save: bool = True
        If True, saves email as .html, .msg, and .txt
    test_email: bool = False
        If True, sends email with "TEST" header to indicate that email is just
        a test.

    Returns
    -------

    """
    test_subject = "[TEST] " if test_email else ""
    report_str = str_from_report(report)

    environment = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = environment.get_template("informational.html")

    distribution_list = get_distribution_list()
    to_list = distribution_list[distribution_list["info"] == "to"]
    cc_list = distribution_list[distribution_list["info"] == "cc"]

    if min_distance > INFO_EMAIL_DISTANCE_THRESHOLD:
        to_list = pd.DataFrame(
            [
                {"email": x.strip(), "name": "INFO_ALWAYS_TO"}
                for x in INFO_ALWAYS_TO.split(";")
                if x
            ]
        )
        cc_list = pd.DataFrame(columns=["email", "name"])

    to_list_chunks, cc_list_chunks = segment_emails(to_list, cc_list)

    cyclone_name = report.get("cyclone").split(" ")[0]

    for to_list_chunk, cc_list_chunk in zip(to_list_chunks, cc_list_chunks):
        msg = EmailMessage()
        msg["Subject"] = (
            f"{test_subject}Vanuatu cyclone monitoring – "
            f"{cyclone_name} forecast information"
        )
        msg["From"] = Address(
            "OCHA Centre for Humanitarian Data",
            EMAIL_ADDRESS.split("@")[0],
            EMAIL_ADDRESS.split("@")[1],
        )
        for mail_list, list_name in zip(
            [to_list_chunk, cc_list_chunk], ["To", "Cc"]
        ):
            msg[list_name] = [
                Address(
                    row["name"],
                    row["email"].split("@")[0],
                    row["email"].split("@")[1],
                )
                for _, row in mail_list.iterrows()
            ]

        map_cid = make_msgid(domain="humdata.org")
        distances_cid = make_msgid(domain="humdata.org")
        chd_banner_cid = make_msgid(domain="humdata.org")
        ocha_logo_cid = make_msgid(domain="humdata.org")

        html_str = template.render(
            name=cyclone_name,
            pub_date=report_str.get("vut_date"),
            pub_time=report_str.get("vut_time"),
            min_distance=min_distance,
            min_hours=min_hours,
            map_cid=map_cid[1:-1],
            distances_cid=distances_cid[1:-1],
            chd_banner_cid=chd_banner_cid[1:-1],
            ocha_logo_cid=ocha_logo_cid[1:-1],
            test_email=test_email,
        )
        text_str = html2text(html_str)
        msg.set_content(text_str)
        msg.add_alternative(html_str, subtype="html")

        for plot, cid in zip(
            ["forecast", "distances"], [map_cid, distances_cid]
        ):
            img_path = (
                OUTPUT_DIR / f"{plot}_plot_{report_str.get('file_dt_str')}.png"
            )
            with open(img_path, "rb") as img:
                msg.get_payload()[1].add_related(
                    img.read(), "image", "png", cid=cid
                )

        for filename, cid in zip(
            ["centre_banner.png", "ocha_logo_wide.png"],
            [chd_banner_cid, ocha_logo_cid],
        ):
            img_path = STATIC_DIR / filename
            with open(img_path, "rb") as img:
                msg.get_payload()[1].add_related(
                    img.read(), "image", "png", cid=cid
                )

        for adm_level in [1, 2]:
            csv_name = (
                f"distances_adm{adm_level}_{report_str.get('file_dt_str')}.csv"
            )
            with open(OUTPUT_DIR / csv_name, "rb") as f:
                f_data = f.read()
            msg.add_attachment(
                f_data, maintype="text", subtype="csv", filename=csv_name
            )

        context = ssl.create_default_context()
        if not suppress_send:
            with smtplib.SMTP_SSL(
                EMAIL_HOST, EMAIL_PORT, context=context
            ) as server:
                server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
                server.sendmail(
                    EMAIL_ADDRESS,
                    to_list_chunk["email"].tolist()
                    + cc_list_chunk["email"].tolist(),
                    msg.as_string(),
                )
        if save:
            file_stem = f"informational_email_{report_str.get('file_dt_str')}"
            with open(OUTPUT_DIR / f"{file_stem}.txt", "w") as f:
                f.write(text_str)
            with open(OUTPUT_DIR / f"{file_stem}.html", "w") as f:
                f.write(html_str)
            with open(OUTPUT_DIR / f"{file_stem}.msg", "wb") as f:
                f.write(bytes(msg))
