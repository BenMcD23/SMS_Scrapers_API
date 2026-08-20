"""Scrape the Service Record → Attendance register from a Bader profile.

Cadet and staff profiles use the same tab dropdown and the same #attendance
DataTable, so both scrapers share this.
"""
from datetime import datetime

from playwright.sync_api import Page

from scripts.tables import ensure_all_rows_shown, entries_total, read_rows, wait_for_full_draw
from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click


def _parse_attendance_row(cells):
    """Turn the column texts of an #attendance row into a record, or None if the
    row isn't a real entry (DataTables pads the tbody with blank rows)."""
    if len(cells) < 3:
        return None
    try:
        date = datetime.strptime(cells[0].strip(), "%d/%m/%Y")
    except ValueError:
        return None
    return {
        "date": date,
        "register_type": cells[1].strip() or None,
        "status": cells[2].strip() or None,
        # Staff registers don't always carry the unit column.
        "unit": cells[3].strip() or None if len(cells) > 3 else None,
    }


def _expected_total(info_text):
    """Row count DataTables claims, from "Showing 1 to 25 of 2,047 entries".
    None if the text isn't in that form."""
    return entries_total(info_text)


def get_attendance(page: Page):
    """Full attendance register for the profile currently open."""
    records = []
    try:
        # "Service Record" opens a dropdown. The Attendance item inside it needs
        # an exact-text match scoped to that dropdown: `contains(text(),
        # 'Attendance')` also resolves the sidebar's "Attendance Registers" link
        # and picks that one first.
        for selector in [
            "xpath=//a[contains(text(), 'Service Record')]",
            "xpath=//div[contains(@class,'dropdown-menu')]//a[normalize-space()='Attendance']",
        ]:
            tab_el = page.wait_for_selector(selector, timeout=15000)
            safe_click(page, tab_el)
            wait_for_preloader(page)
            wait_for_aspx_load(page)

        # The table the second click opens is what the fixed sleeps after each
        # click were really standing in for — wait on it instead.
        page.wait_for_selector("#attendance", timeout=15000)

        # DataTables shows 10 rows by default and drops the rest from the DOM —
        # without switching to "All" we'd silently scrape a tenth of the history.
        # required=False: too few rows and DataTables renders no length control.
        ensure_all_rows_shown(page, "attendance_length", "attendance", required=False)
        wait_for_full_draw(page, "attendance")

        # Read the whole table in a single evaluate(). Walking it with
        # query_selector_all + inner_text() costs a CDP round-trip per cell,
        # which is minutes for staff with registers going back to 2009.
        for cells in read_rows(page, "#attendance"):
            record = _parse_attendance_row(cells)
            if record:
                records.append(record)

        # Callers full-replace on a non-empty result, so a partial read would
        # swap years of history for a handful of rows. wait_for_full_draw should
        # have prevented that; say so if it somehow didn't.
        info_el = page.query_selector("#attendance_info")
        expected = _expected_total(info_el.inner_text() if info_el else None)
        if expected is not None and len(records) < expected:
            print(f"Warning: attendance read {len(records)} of {expected} rows — table may not have finished rendering")

    except Exception as e:
        print(f"Warning: Could not extract attendance: {e}")
    return records
