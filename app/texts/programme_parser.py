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

from core.config import IMPERSONATE_EMAIL, PROGRAMME_DRIVE_FOLDER_ID
from core.security import _service_account_creds

# Full names or the usual abbreviations ("Wed 2nd", "Thurs 4th") — a row whose
# weekday isn't stripped fails to parse and the night silently disappears.
WEEKDAY_RE = re.compile(
    r"\b(Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)(?:day|nesday|sday|urday)?\b\.?,?", re.IGNORECASE
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
    """Parse the date cell (weekday already stripped) against the known month/year.

    None for anything that isn't a real date — a typo like "31/02" in one row
    must skip that night, not fail the whole month's generation."""
    try:
        return _parse_date_strict(raw, month, year)
    except ValueError:
        return None


def _parse_date_strict(raw: str, month: int, year: int) -> datetime | None:
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


def _read_table(month: int, year: int) -> tuple[dict, _Columns]:
    drive, docs = _docs_clients()
    doc_id = _find_programme_doc_id(drive, month, year)
    document = docs.documents().get(documentId=doc_id).execute()

    table = _first_table(document)
    if table is None:
        raise ValueError("Programme doc has no table")
    return table, _Columns(table)


def _walk_nights(table: dict, columns: _Columns, month: int, year: int) -> list[dict]:
    """[{date, uniform, dnco, cells}] for each night with a real date, in
    order. Blank-date rows are rowSpan continuations: their uniform and DNCO
    are added to the night above. Shared by the texts (parse_programme) and
    the programme record (parse_programme_sessions) so both read the same
    nights the same way."""
    nights: list[dict] = []
    for table_row in table.get("tableRows", [])[2:]:
        cells = table_row.get("tableCells", [])

        def at(col: int) -> str:
            # Only ever called within this iteration, so binding the loop's
            # `cells` late is intended.
            return _cell_text(cells[col]) if col < len(cells) else ""  # noqa: B023

        date_raw = WEEKDAY_RE.sub("", at(columns.date)).strip()
        uniform = at(columns.uniform)
        dnco = at(columns.dnco)

        if not date_raw:
            if nights:
                last = nights[-1]
                if uniform:
                    last["uniform"] += (", " if last["uniform"] else "") + uniform
                if dnco:
                    last["dnco"] += (", " if last["dnco"] else "") + dnco
            continue
        nights.append({"date_raw": date_raw, "uniform": uniform, "dnco": dnco, "cells": cells})

    result = []
    for night in nights:
        date = _parse_date(night.pop("date_raw"), month, year)
        if date is not None:
            night["date"] = date
            result.append(night)
    return result


def parse_programme(month: int, year: int) -> list[dict]:
    """Return [{date, uniform, dnco, c_flight, main_body}] for each parade night."""
    table, columns = _read_table(month, year)
    result = []
    for night in _walk_nights(table, columns, month, year):
        c_parts, main_parts = [], []
        for label, (c_col, a_cols, b_cols) in zip(["1st Period", "2nd Period"], columns.periods, strict=False):
            c_text, main_section = _parse_period(night["cells"], c_col, a_cols, b_cols)
            if c_text:
                c_parts.append(f"{label}:\n{c_text}")
            if main_section:
                main_parts.append(f"{label}\n{main_section}")
        result.append({
            "date": night["date"],
            "uniform": night["uniform"],
            "dnco": night["dnco"],
            "c_flight": "\n\n".join(c_parts),
            "main_body": "\n\n".join(main_parts),
        })
    return result


# "As per squadron" in the probationary column: C Flight joins in with
# everyone else rather than having its own lesson.
AS_PER_SQUADRON = re.compile(r"^\s*as\s+per\s+(the\s+)?squadron\s*$", re.IGNORECASE)


def _period_sessions(cells: list[dict], c_col: int, a_cols: list[int], b_cols: list[int]) -> list[dict]:
    """What each flight did in one period, as [{flights, text, split}]: the
    same merged-cell rules as _parse_period, kept as data instead of a text.

    `split` marks a flight divided between parallel activities (several cells
    in its sub-columns), so attendance can't say which one a cadet was in.
    C Flight "as per squadron" goes with A and B only when they're together;
    when they're doing different things which one C joined isn't knowable,
    so C gets nothing for that period rather than a guess."""
    def text(col):
        return _cell_text(cells[col]) if col < len(cells) else ""

    def span(col):
        return _cell_span(cells[col]) if col < len(cells) else 1

    c_text = text(c_col)
    if c_text and span(c_col) > 1 and c_col + span(c_col) > max(b_cols):
        return [{"flights": ["C", "A", "B"], "text": c_text, "split": False}]

    sessions = []
    joins = bool(c_text) and bool(AS_PER_SQUADRON.match(c_text))
    if c_text and not joins:
        sessions.append({"flights": ["C"], "text": c_text, "split": False})

    a_head = text(a_cols[0])
    if a_head and a_cols[0] + span(a_cols[0]) > max(b_cols):
        flights = ["C", "A", "B"] if joins else ["A", "B"]
        return [*sessions, {"flights": flights, "text": a_head, "split": False}]

    for flight, cols in (("A", a_cols), ("B", b_cols)):
        texts = [t for t in (text(col) for col in cols) if t]
        for t in texts:
            sessions.append({"flights": [flight], "text": t, "split": len(texts) > 1})
    return sessions


def parse_programme_sessions(month: int, year: int) -> list[dict]:
    """[{date, uniform, dnco, sessions: [{period, flights, text, split}]}]:
    the programme as a record of what each flight did, for
    core/programme.py to tidy and store."""
    table, columns = _read_table(month, year)
    return [
        {
            "date": night["date"],
            "uniform": night["uniform"],
            "dnco": night["dnco"],
            "sessions": [
                {"period": period, **session}
                for period, (c_col, a_cols, b_cols) in enumerate(columns.periods, start=1)
                for session in _period_sessions(night["cells"], c_col, a_cols, b_cols)
            ],
        }
        for night in _walk_nights(table, columns, month, year)
    ]
