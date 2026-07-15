from playwright.sync_api import Page
from datetime import datetime

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

    # Show all rows (like the quali scraper does with Cadets_length).
    try:
        page.locator("[name='unitAbsences_length']").select_option(value="-1")
        wait_for_aspx_load(page)
    except Exception:
        pass  # dropdown absent when there are 0 absences

    absences = []
    rows = page.query_selector_all("#unitAbsences tbody tr")
    for row in rows:
        cols = row.query_selector_all("td")
        if len(cols) < 6:
            continue  # "No data available" placeholder row
        first_name = cols[0].inner_text().strip()
        last_name = cols[1].inner_text().strip()
        date_from = _parse_date(cols[3].inner_text())
        date_to = _parse_date(cols[4].inner_text())
        reason = cols[5].inner_text().strip()
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
