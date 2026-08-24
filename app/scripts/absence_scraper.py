from playwright.sync_api import Page
from datetime import datetime

from scripts.tables import ensure_all_rows_shown, read_rows, wait_for_full_draw
from scripts.waiter import wait_for_aspx_load, wait_for_preloader

ABSENCES_URL = "https://sms.bader.mod.uk/units/common/unitAbsences.aspx"


def _parse_date(s: str):
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y")
    except (ValueError, AttributeError):
        return None


def get_absences(page: Page):
    """Scrape every row of the unit absences table.

    The page auto-fills the current date, so it only lists current + future
    absences. Returns [{first_name, last_name, date_from, date_to, reason}].
    """
    page.goto(ABSENCES_URL)
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    # Show all rows. required=False: the dropdown is absent when there are 0
    # absences, and wait_for_aspx_load doesn't cover a client-side DataTables
    # redraw, so wait on the drawn row count instead.
    ensure_all_rows_shown(page, "unitAbsences_length", "unitAbsences", required=False)
    wait_for_full_draw(page, "unitAbsences")

    return _parse_absence_rows(read_rows(page, "#unitAbsences"))


def _parse_absence_rows(rows):
    absences = []
    for cols in rows:
        if len(cols) < 6:
            continue  # "No data available" placeholder row
        first_name = cols[0].strip()
        last_name = cols[1].strip()
        date_from = _parse_date(cols[3])
        date_to = _parse_date(cols[4])
        reason = cols[5].strip()
        if not (first_name and last_name and date_from and date_to):
            continue
        absences.append({
            "first_name": first_name,
            "last_name": last_name,
            "date_from": date_from,
            "date_to": date_to,
            "reason": reason,
        })
    return absences


if __name__ == "__main__":
    # Bader renders dates as dd/mm/yyyy — a US-style parse would swap the AWOL
    # window silently, so pin the format.
    assert _parse_date("09/07/2026") == datetime(2026, 7, 9)
    assert _parse_date("31/07/2026") == datetime(2026, 7, 31)
    assert _parse_date("") is None and _parse_date("bad") is None
    print("absence_scraper date parsing OK")
