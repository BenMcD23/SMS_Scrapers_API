"""Pulls the monthly programme Google Doc and extracts one entry per parade night.

The programme doc lives in a year-named subfolder of PROGRAMME_DRIVE_FOLDER_ID
and is named "MM_YY". Its first table looks like (12 columns in June 2026):

    Date | Probationary | A Flight | B Flight | Break | Probationary | A Flight.. | B Flight.. | Uniform | Duty NCO
         |  (1st period C Flight)  ...        |       |  (2nd period)             ...          |         |

Column positions are derived from the two header rows rather than hardcoded,
since the A/B flight columns can be subdivided (a flight doing two parallel
activities occupies two sub-columns). Merged cells matter semantically:

  - a cell spanning a flight's sub-columns        -> one activity for that flight
  - a cell spanning both A and B                  -> both flights together
  - a cell spanning C through B                   -> whole squadron
  - several cells within one flight's sub-columns -> cadets split between them
  - a row with a blank date (rowSpan continuation) merges its uniform/DNCO
    into the night above (e.g. "No.2a SD" + "Civvies")
"""

import re
from datetime import datetime

from googleapiclient.discovery import build as google_build

from core.config import PROGRAMME_DRIVE_FOLDER_ID
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
    # Uses the service account's *own* identity (no domain-wide delegation) — the
    # programme folder is shared directly with the SA, so this can only read that
    # folder rather than every user's Drive.
    creds = _service_account_creds([
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/documents.readonly",
    ])
    drive = google_build("drive", "v3", credentials=creds, cache_discovery=False)
    docs = google_build("docs", "v1", credentials=creds, cache_discovery=False)
    return drive, docs


def _list_children(drive, parent_id: str, name: str, mime_type: str) -> list[dict]:
    # Shared-drive flags are required or items on shared drives are silently omitted
    return drive.files().list(
        q=(f"'{parent_id}' in parents and name = '{name}' "
           f"and mimeType = '{mime_type}' and trashed = false"),
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute().get("files", [])


def _find_programme_doc_id(drive, month: int, year: int) -> str:
    """Locate the "MM_YY" doc inside the year folder, e.g. 2026/01_26."""
    folders = _list_children(
        drive, PROGRAMME_DRIVE_FOLDER_ID, str(year),
        "application/vnd.google-apps.folder",
    )
    if not folders:
        raise FileNotFoundError(f"No '{year}' folder in the programme Drive folder")

    doc_name = f"{month:02d}_{str(year)[-2:]}"
    docs_found = _list_children(
        drive, folders[0]["id"], doc_name,
        "application/vnd.google-apps.document",
    )
    if not docs_found:
        raise FileNotFoundError(f"No '{doc_name}' programme doc in the {year} folder")
    return docs_found[0]["id"]


def _cell_text(cell: dict) -> str:
    parts = []
    for content in cell.get("content", []):
        for el in content.get("paragraph", {}).get("elements", []):
            parts.append(el.get("textRun", {}).get("content", ""))
    # Docs uses \x0b for soft line breaks (shift+enter) — treat them as newlines
    return "".join(parts).replace("\x0b", "\n").strip()


def _cell_span(cell: dict) -> int:
    return cell.get("tableCellStyle", {}).get("columnSpan", 1) or 1


def _first_table(document: dict) -> dict | None:
    for el in document.get("body", {}).get("content", []):
        if "table" in el:
            return el["table"]
    return None


def _normalise_headers(row: dict, num_cols: int) -> list[str]:
    """Column -> header text. Covered cells exist in the JSON, so fill them
    with the preceding head cell's text when that head spans them."""
    labels = [""] * num_cols
    cells = row.get("tableCells", [])
    for idx, cell in enumerate(cells[:num_cols]):
        text = _cell_text(cell)
        if not text:
            continue
        for col in range(idx, min(idx + _cell_span(cell), num_cols)):
            labels[col] = text
    return labels


class _Columns:
    """Column indices derived from the two header rows."""

    def __init__(self, table: dict):
        rows = table.get("tableRows", [])
        if len(rows) < 3:
            raise ValueError("Programme table is too short")
        num_cols = table.get("columns", 0)

        top = _normalise_headers(rows[0], num_cols)
        sub = _normalise_headers(rows[1], num_cols)

        def find(predicate) -> list[int]:
            return [i for i in range(num_cols) if predicate(top[i].lower(), sub[i].lower())]

        date_cols = find(lambda t, s: "date" in t)
        prob_cols = find(lambda t, s: "probationary" in t)
        uniform_cols = find(lambda t, s: "uniform" in t)
        dnco_cols = find(lambda t, s: "nco" in t)
        p1_a = find(lambda t, s: "1st" in t and "a flight" in s)
        p1_b = find(lambda t, s: "1st" in t and "b flight" in s)
        p2_a = find(lambda t, s: "2nd" in t and "a flight" in s)
        p2_b = find(lambda t, s: "2nd" in t and "b flight" in s)

        if not (date_cols and len(prob_cols) >= 2 and uniform_cols and dnco_cols
                and p1_a and p1_b and p2_a and p2_b):
            raise ValueError(
                "Could not identify the programme table columns — has the layout changed?"
            )

        self.date = date_cols[0]
        self.c1, self.c2 = prob_cols[0], prob_cols[-1]
        self.uniform = uniform_cols[0]
        self.dnco = dnco_cols[0]
        self.periods = [(self.c1, p1_a, p1_b), (self.c2, p2_a, p2_b)]


def _collect_flight(cells: list[dict], flight_cols: list[int]) -> str:
    """Activities within one flight's sub-columns; parallel ones joined with ' / '."""
    texts = []
    for col in flight_cols:
        if col < len(cells):
            text = _cell_text(cells[col])
            if text:
                texts.append(text)
    if len(texts) <= 1:
        return texts[0] if texts else ""
    # Several sub-activities the flight is split between — match the doc's own
    # "/" convention, collapsing each activity's lines so the pairing stays clear
    return " / ".join(", ".join(t.splitlines()) for t in texts)


def _parse_period(cells: list[dict], c_col: int, a_cols: list[int], b_cols: list[int]):
    """Returns (c_text, main_section) for one period of a night's row."""
    c_text = _cell_text(cells[c_col]) if c_col < len(cells) else ""
    c_span = _cell_span(cells[c_col]) if c_col < len(cells) else 1

    # Whole squadron: the probationary cell spans through the A/B columns
    if c_text and c_span > 1 and c_col + c_span > max(b_cols):
        return c_text, f"Whole Squadron:\n{c_text}"

    a_head = cells[a_cols[0]] if a_cols[0] < len(cells) else {}
    a_text = _cell_text(a_head)
    # Both flights together: the A-flight cell spans into the B-flight columns
    if a_text and a_cols[0] + _cell_span(a_head) > max(b_cols):
        return c_text, f"Both Flights:\n{a_text}"

    a = _collect_flight(cells, a_cols)
    b = _collect_flight(cells, b_cols)
    parts = []
    if a:
        parts.append(f"A Flight:\n{a}")
    if b:
        parts.append(f"B Flight:\n{b}")
    return c_text, "\n\n".join(parts)


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

    columns = _Columns(table)

    nights: list[dict] = []
    last: dict | None = None

    for table_row in table.get("tableRows", [])[2:]:
        cells = table_row.get("tableCells", [])

        def at(col: int) -> str:
            return _cell_text(cells[col]) if col < len(cells) else ""

        date_raw = WEEKDAY_RE.sub("", at(columns.date)).strip()
        uniform = at(columns.uniform)
        dnco = at(columns.dnco)

        # Blank-date rows are rowSpan continuations — extra uniform/DNCO values
        if not date_raw:
            if last is not None:
                if uniform:
                    last["uniform"] += (", " if last["uniform"] else "") + uniform
                if dnco:
                    last["dnco"] += (", " if last["dnco"] else "") + dnco
            continue

        c_parts, main_parts = [], []
        for label, (c_col, a_cols, b_cols) in zip(["1st Period", "2nd Period"], columns.periods):
            c_text, main_section = _parse_period(cells, c_col, a_cols, b_cols)
            if c_text:
                c_parts.append(f"{label}:\n{c_text}")
            if main_section:
                main_parts.append(f"{label}\n{main_section}")

        if last is not None:
            nights.append(last)
        last = {
            "date_raw": date_raw,
            "uniform": uniform,
            "dnco": dnco,
            "c_flight": "\n\n".join(c_parts),
            "main_body": "\n\n".join(main_parts),
        }

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
