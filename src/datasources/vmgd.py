"""Parsers for the VMGD historical TC archive.

The archive is a single ~9 GB zip on blob (see
``ds-aa-vut-cyclones/raw/vmgd/vmgd_historical_tc_archive_2026-08-05.zip``).
It holds two things we can use quantitatively:

1. **ATCF decks** (``*.dat``) — JTWC a-decks with quadrant wind radii at
   34/50/64 kt. These are the only source of forecast radii in the archive.
2. **VMGD Forecast Track Maps** (``Forecast_Track_*.docx``) — VMGD's own
   forecast positions and intensity categories. These carry **no wind
   radii**; the wind boundaries exist only as rings drawn on the map image.

Note the zip is zip64 — Info-ZIP (``unzip``/``zipinfo``) reports it corrupt.
Python's ``zipfile`` reads it fine.
"""

import re
import zipfile
from io import BytesIO
from typing import Optional

import numpy as np
import pandas as pd

from src.constants import LOCAL_TIMEZONE

# ATCF a-deck column positions (0-indexed, comma-delimited)
_ATCF = {
    "basin": 0,
    "cy": 1,
    "init": 2,
    "tech": 4,
    "tau": 5,
    "lat": 6,
    "lon": 7,
    "vmax": 8,
    "mslp": 9,
    "ty": 10,
    "rad": 11,
    "windcode": 12,
    "r1": 13,
    "r2": 14,
    "r3": 15,
    "r4": 16,
}

QUADS = ("ne", "se", "sw", "nw")


def _atcf_latlon(tok: str) -> Optional[float]:
    """``'136S'`` -> ``-13.6``; ``'1701E'`` -> ``170.1``."""
    m = re.match(r"^(\d+)([NSEW])$", tok or "")
    if not m:
        return None
    v = int(m.group(1)) / 10.0
    return -v if m.group(2) in ("S", "W") else v


def parse_atcf_deck(text: str, storm: str, deck: str) -> list:
    """Parse one ATCF deck file into a list of row dicts.

    Handles the ``AAA`` windcode (radius applies to the full circle, given
    in RAD1) by broadcasting RAD1 to all four quadrants.
    """
    rows = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 17:
            continue
        lat = _atcf_latlon(p[_ATCF["lat"]])
        lon = _atcf_latlon(p[_ATCF["lon"]])
        if lat is None or lon is None:
            continue
        try:
            tau = int(p[_ATCF["tau"]])
            rad = int(p[_ATCF["rad"]] or 0)
            quads = [int(p[_ATCF[c]] or 0) for c in ("r1", "r2", "r3", "r4")]
            vmax = int(p[_ATCF["vmax"]] or 0)
        except ValueError:
            continue

        windcode = p[_ATCF["windcode"]]
        if windcode == "AAA":
            # full-circle radius given in RAD1
            quads = [quads[0]] * 4

        rows.append(
            {
                "storm": storm,
                "deck": deck,
                "tech": p[_ATCF["tech"]],
                "init": p[_ATCF["init"]],
                "tau": tau,
                "lat": lat,
                # keep longitude in [0, 360) — Vanuatu sits near the dateline
                "lon": lon % 360,
                "vmax": vmax,
                "rad": rad,
                "ne": quads[0],
                "se": quads[1],
                "sw": quads[2],
                "nw": quads[3],
                "name": p[27].strip() if len(p) > 27 else "",
            }
        )
    return rows


def parse_atcf_from_archive(zip_path: str) -> pd.DataFrame:
    """Parse every ``*.dat`` ATCF deck in the archive into a tidy frame.

    Returns one row per (storm, tech, init, tau, rad-threshold).
    """
    z = zipfile.ZipFile(zip_path)
    rows = []
    for name in z.namelist():
        if not name.lower().endswith(".dat"):
            continue
        fn = name.rsplit("/", 1)[-1]
        m = re.match(r"([ab])(sh|wp|sp)(\d\d)(\d{4})", fn.lower())
        if not m:
            continue
        storm = f"{m.group(2).upper()}{m.group(3)}{m.group(4)}"
        text = z.read(name).decode("utf-8", "replace")
        rows.extend(parse_atcf_deck(text, storm, m.group(1)))

    df = pd.DataFrame(rows)
    df["init"] = pd.to_datetime(df["init"], format="%Y%m%d%H", utc=True)
    df["valid_time"] = df["init"] + pd.to_timedelta(df["tau"], unit="h")
    # later files in the archive supersede earlier duplicates
    df = df.drop_duplicates(
        subset=["storm", "tech", "init", "tau", "rad"], keep="last"
    )
    return df.reset_index(drop=True)


def wide_radii(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot the long (one row per radius threshold) ATCF frame to wide.

    Produces ``r{speed}_{quad}`` columns for speeds 34/50/64.
    """
    idx = ["storm", "tech", "init", "tau", "valid_time", "lat", "lon", "vmax"]
    out = df[df["rad"].isin([34, 50, 64])].pivot_table(
        index=idx, columns="rad", values=list(QUADS), aggfunc="max"
    )
    out.columns = [f"r{int(s)}_{q}" for q, s in out.columns]
    out = out.reset_index()

    # rows with no radii at all still need the columns present
    for speed in (34, 50, 64):
        for q in QUADS:
            col = f"r{speed}_{q}"
            if col not in out:
                out[col] = np.nan

    base = df[idx].drop_duplicates()
    out = base.merge(out, on=idx, how="left")
    return out.sort_values(["storm", "init", "tau"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# VMGD Forecast Track Map docx
# --------------------------------------------------------------------------

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)


def _docx_tables(blob: bytes) -> list:
    """Return each table as a list of rows, each row a list of cell strings."""
    with zipfile.ZipFile(BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")

    tables = []
    for tbl in re.findall(r"<w:tbl[ >].*?</w:tbl>", xml, re.S):
        rows = []
        for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S):
            cells = []
            for tc in re.findall(r"<w:tc[ >].*?</w:tc>", tr, re.S):
                # NB: `<w:t[^>]*>` would also match `<w:tcPr>` and swallow
                # the formatting XML — require a space or an immediate close.
                texts = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", tc, re.S)
                cell = "".join(texts)
                cell = (
                    cell.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                )
                cells.append(re.sub(r"\s+", " ", cell).strip())
            rows.append(cells)
        tables.append(rows)
    return tables


def _docx_text(blob: bytes) -> str:
    with zipfile.ZipFile(BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    txt = re.sub(r"<[^>]+>", "", xml)
    txt = txt.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", txt)


def _vmgd_latlon(tok: str) -> Optional[float]:
    """``'17.2S'`` -> ``-17.2``; ``'160.6E'`` -> ``160.6``."""
    m = re.match(r"^\s*([\d.]+)\s*([NSEW])\s*$", tok or "", re.I)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return -v if m.group(2).upper() in ("S", "W") else v


def parse_vmgd_forecast_docx(blob: bytes, source: str) -> list:
    """Parse one VMGD Forecast Track Map docx into forecast-position rows.

    These documents have **no wind radii** — only position, intensity
    category and an estimated position accuracy.
    """
    text = _docx_text(blob)

    m = re.search(
        r"Track Map\s*Number\s*(\d+)\s*issued at\s*(.{0,60}?)\s*"
        rf"({_MONTHS})\s+(\d{{4}})",
        text,
        re.I | re.S,
    )
    number = int(m.group(1)) if m else None
    issue_year = int(m.group(4)) if m else None

    m_issue = re.search(
        rf"issued at\s*([\d:]+\s*[ap]\.?m\.?)\s*(?:VUT)?\s*\w*day\s*"
        rf"(\d{{1,2}})\s+({_MONTHS})\s+(\d{{4}})",
        text,
        re.I,
    )
    issue_time = None
    if m_issue:
        try:
            issue_time = pd.to_datetime(
                f"{m_issue.group(2)} {m_issue.group(3)} {m_issue.group(4)} "
                f"{m_issue.group(1).replace('.', '').upper()}",
                format="%d %B %Y %I:%M %p",
            )
        except ValueError:
            issue_time = None

    name_m = re.search(r"Name:\s*(.+)", text)
    storm_name = name_m.group(1).strip() if name_m else ""

    rows = []
    for table in _docx_tables(blob):
        header = " ".join(table[0]).lower() if table else ""
        if "latitude" not in header:
            continue
        for cells in table[1:]:
            cells = [c for c in cells if c != ""]
            if len(cells) < 4:
                continue
            tau_m = re.match(r"^\+?(\d+)\s*hr", cells[0], re.I)
            if not tau_m:
                continue
            lat = lon = None
            for i, c in enumerate(cells):
                v = _vmgd_latlon(c)
                if v is not None:
                    if lat is None and re.search(r"[NS]\s*$", c, re.I):
                        lat = v
                    elif lon is None and re.search(r"[EW]\s*$", c, re.I):
                        lon = v
            if lat is None or lon is None:
                continue
            rows.append(
                {
                    "source": source,
                    "number": number,
                    "issue_time": issue_time,
                    "storm_name": storm_name,
                    "tau": int(tau_m.group(1)),
                    "category": cells[2] if len(cells) > 2 else "",
                    "lat": lat,
                    "lon": lon % 360,
                    "year": issue_year,
                }
            )
    return rows


def parse_vmgd_forecasts_from_archive(zip_path: str) -> pd.DataFrame:
    """Parse every VMGD Forecast Track Map docx in the archive."""
    z = zipfile.ZipFile(zip_path)
    names = [
        n
        for n in z.namelist()
        if re.search(r"forecast[ _]?_?track", n, re.I)
        and n.lower().endswith(".docx")
    ]
    rows = []
    for n in names:
        try:
            rows.extend(parse_vmgd_forecast_docx(z.read(n), n))
        except (zipfile.BadZipFile, KeyError):
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["season_dir"] = df["source"].str.split("/").str[1]
    df["event_dir"] = df["source"].str.split("/").str[2]
    # issue times in these documents are VUT local; convert to UTC so they
    # line up with the ATCF init times
    df["issue_time"] = (
        df["issue_time"]
        .dt.tz_localize(
            LOCAL_TIMEZONE, ambiguous=True, nonexistent="shift_forward"
        )
        .dt.tz_convert("UTC")
    )
    df["valid_time"] = df["issue_time"] + pd.to_timedelta(df["tau"], unit="h")
    return df.reset_index(drop=True)
