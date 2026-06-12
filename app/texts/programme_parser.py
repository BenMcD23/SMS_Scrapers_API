"""Pulls the monthly programme Google Doc and extracts one entry per parade night.

Port of the Apps Script "Programme to Text" parser. The programme doc lives in
a year-named subfolder of PROGRAMME_DRIVE_FOLDER_ID and is named "MM_YY". Its
first table has (from row index 2):

    col 0: date    col 1-3: C/A/B flight first half    col 5-7: C/A/B second half
    col 8: uniform    col 9: DNCO

Rows with a blank date merge their uniform/DNCO into the previous night.
"""

import re
from datetime import datetime

from googleapiclient.discovery import build as google_build

from core.config import PROGRAMME_DRIVE_FOLDER_ID, IMPERSONATE_EMAIL
from core.security import _service_account_creds

WEEKDAY_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)", re.IGNORECASE
)
ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)", re.IGNORECASE)

MONTH_NAMES = {
    name.lower(): i
    for i, name in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1
    )
}


def _docs_clients():
    creds = _service_account_creds([
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/documents.readonly",
    ]).with_subject(IMPERSONATE_EMAIL)
    drive = google_build("drive", "v3", credentials=creds, cache_discovery=False)
    docs = google_build("docs", "v1", credentials=creds, cache_discovery=False)
    return drive, docs


def _find_programme_doc_id(drive, month: int, year: int) -> str:
    """Locate the "MM_YY" doc inside the year folder, e.g. 2026/01_26."""
    folders = drive.files().list(
        q=(f"'{PROGRAMME_DRIVE_FOLDER_ID}' in parents and name = '{year}' "
           "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"),
        fields="files(id)",
    ).execute().get("files", [])
    if not folders:
        raise FileNotFoundError(f"No '{year}' folder in the programme Drive folder")

    doc_name = f"{month:02d}_{str(year)[-2:]}"
    docs_found = drive.files().list(
        q=(f"'{folders[0]['id']}' in parents and name = '{doc_name}' "
           "and mimeType = 'application/vnd.google-apps.document' and trashed = false"),
        fields="files(id)",
    ).execute().get("files", [])
    if not docs_found:
        raise FileNotFoundError(f"No '{doc_name}' programme doc in the {year} folder")
    return docs_found[0]["id"]


def _cell_text(cell: dict) -> str:
    parts = []
    for content in cell.get("content", []):
        for el in content.get("paragraph", {}).get("elements", []):
            parts.append(el.get("textRun", {}).get("content", ""))
    return "".join(parts).strip()


def _first_table(document: dict) -> dict | None:
    for el in document.get("body", {}).get("content", []):
        if "table" in el:
            return el["table"]
    return None


def _parse_date(raw: str, month: int, year: int) -> datetime | None:
    """Parse the date cell (weekday already stripped) against the known month/year."""
    text = ORDINAL_RE.sub(r"\1", raw).strip()
    if not text:
        return None

    # "14/01/26", "14/01/2026", "14/01" or "14-01"
    m = re.match(r"^(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*(\d{2,4}))?$", text)
    if m:
        day, mon = int(m.group(1)), int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else year
        if yr < 100:
            yr += 2000
        return datetime(yr, mon, day)

    # "14 January", "14 Jan", or just "14"
    m = re.match(r"^(\d{1,2})(?:\s+([A-Za-z]+))?(?:\s+(\d{2,4}))?$", text)
    if m:
        day = int(m.group(1))
        mon = month
        if m.group(2):
            name = m.group(2).lower()
            mon = next((v for k, v in MONTH_NAMES.items() if k.startswith(name)), month)
        yr = int(m.group(3)) if m.group(3) else year
        if yr < 100:
            yr += 2000
        return datetime(yr, mon, day)

    return None


def parse_programme(month: int, year: int) -> list[dict]:
    """Return [{date, uniform, dnco, c_flight, main_body}] for each parade night."""
    drive, docs = _docs_clients()
    doc_id = _find_programme_doc_id(drive, month, year)
    document = docs.documents().get(documentId=doc_id).execute()

    table = _first_table(document)
    if table is None:
        raise ValueError("Programme doc has no table")

    nights: list[dict] = []
    last: dict | None = None

    for table_row in table.get("tableRows", [])[2:]:
        cells = [_cell_text(c) for c in table_row.get("tableCells", [])]
        cells += [""] * (10 - len(cells))  # pad short rows
        cells[0] = WEEKDAY_RE.sub("", cells[0]).strip()

        c1, a1, b1 = cells[1], cells[2], cells[3]
        c2, a2, b2 = cells[5], cells[6], cells[7]

        combined_c = (c1 or "") + (f"\n{c2}" if c2 else "")

        a_content: list[str] = []
        b_content: list[str] = []
        for c, a, b in ((c1, a1, b1), (c2, a2, b2)):
            if c and not a and not b:
                a_content.append(c)       # whole squadron doing the same thing
            elif c and a and not b:
                a_content.append(a)       # C and A split, B joins A
            else:
                if a:
                    a_content.append(a)
                if b:
                    b_content.append(b)

        if a_content and not b_content:
            combined_m = "\n".join(a_content)
        else:
            combined_m = ""
            if a_content:
                combined_m += "A Flight:\n" + "\n".join(a_content)
            if b_content:
                combined_m += ("\n\n" if combined_m else "") + "B Flight:\n" + "\n".join(b_content)

        row = {
            "date_raw": cells[0],
            "uniform": cells[8],
            "dnco": cells[9],
            "c_flight": combined_c,
            "main_body": combined_m,
        }

        # Blank-date rows merge uniform/DNCO into the previous night
        if row["date_raw"] == "" and last is not None:
            if row["uniform"]:
                last["uniform"] += ", " + row["uniform"]
            if row["dnco"]:
                last["dnco"] += ", " + row["dnco"]
        else:
            if last is not None:
                nights.append(last)
            last = row

    if last is not None:
        nights.append(last)

    result = []
    for night in nights:
        date = _parse_date(night.pop("date_raw"), month, year)
        if date is None:
            continue
        night["date"] = date
        result.append(night)
    return result
