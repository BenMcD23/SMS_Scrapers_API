"""Parents' email addresses from Bader's "Next Of Kin - Mail Merge" report.

The report lists every member of the unit, grouped under a Personnel Type
heading row (CDT, CI, ...), with one row per next of kin. Only the CDT group
is read: those are the cadets the OC's leaving and BCC lists are about.
"""

from datetime import datetime

from playwright.sync_api import Page

from core.emailer import EMAIL_RE
from scripts.reports import page_through_report, read_report_cells

NOK_URL = "https://sms.bader.mod.uk/reports/unitPersonnelNoKMailMerge.aspx"
NOK_VIEWER = "rptvwUnitPersonnelNoKMailMerge"
CADET_TYPE = "CDT"

# spacer, Personnel Type, Service Number, Forename, Surname, DOB, Relationship, Email
ROW_CELLS = 8


def _parse_dob(text):
    # The report is rendered en-US (9/29/2011), unlike the dd/mm/yyyy pages.
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y")
    except ValueError:
        return None


def _parse_nok_rows(rows, current=None):
    """(next-of-kin records under the CDT heading, the heading in force at the end).

    ``current`` carries the heading over from the previous page: a group can
    run across a page break, and the continuation page has no heading of its own.
    """
    records = []
    for cells in rows:
        if len(cells) != ROW_CELLS:
            continue  # layout rows wrapping the table
        personnel_type, service_no, first, last, dob, relationship, email = (c.strip() for c in cells[1:])
        if personnel_type == "Personnel Type":
            continue  # column headings, repeated on every page
        if personnel_type:
            current = personnel_type
            continue
        if current != CADET_TYPE or not (first and last):
            continue
        records.append({
            "service_number": service_no,
            "first_name": first,
            "last_name": last,
            "date_of_birth": _parse_dob(dob),
            "relationship": relationship,
            "email": email,
        })
    return records, current


def get_parent_emails(page: Page):
    """Every CDT next-of-kin row across all pages of the report.

    Raises if the report never draws, so the caller can tell "Bader has no
    addresses" apart from "the report didn't load" and leave the stored ones be.
    """
    records = []
    current = None

    def read_page(p):
        nonlocal current
        found, current = _parse_nok_rows(read_report_cells(p), current)
        records.extend(found)

    page_through_report(page, NOK_URL, NOK_VIEWER, read_page)
    return records


def valid_email(email: str) -> bool:
    return bool(email) and bool(EMAIL_RE.match(email))


def _key(text):
    return " ".join((text or "").split()).casefold()


def match_cadet(record, cadets):
    """CIN of the cadet a report row is about, or None.

    The report's service number isn't the CIN, so it's by name. Exact forename
    and surname first; failing that (or when two cadets share a name) the date of
    birth picks one out of the same-surname cadets, which also catches a Bader
    forename that differs from the one we store. Never guesses between two.
    """
    first, last, dob = _key(record["first_name"]), _key(record["last_name"]), record["date_of_birth"]
    same_name = [c for c in cadets if _key(c.first_name) == first and _key(c.last_name) == last]
    if len(same_name) == 1:
        return same_name[0].cin
    if dob is None:
        return None
    candidates = same_name or [c for c in cadets if _key(c.last_name) == last]
    by_dob = [c for c in candidates if c.date_of_birth and c.date_of_birth.date() == dob.date()]
    return by_dob[0].cin if len(by_dob) == 1 else None


def merge_emails(stored: str | None, scraped: list[str]) -> str | None:
    """What's stored, plus any of Bader's addresses that aren't there yet.

    Additive on purpose: the OC entered these before there was a scraper, and an
    address they typed must not vanish because Bader lacks it. One the OC deletes
    comes back while Bader still lists it. Stored order is kept, so a run that
    finds nothing new leaves the value byte-for-byte as it was.
    """
    seen: dict[str, str] = {}
    for email in [*(stored or "").split(";"), *scraped]:
        email = email.strip()
        if email:
            seen.setdefault(email.lower(), email)
    return ";".join(seen.values()) or None
