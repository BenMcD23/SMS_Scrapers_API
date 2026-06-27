from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from datetime import date
import calendar
import json

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click
from scripts.scraper_utils import init_scraper, login, match_email

from database.models import Staff
from google_admin_api.get_all_users import get_workspace_users


def get_staff(page: Page):
    """Scrape the staff roster from SMS. Returns list of {first_name, last_name, rank, cin}."""
    page.goto("https://sms.bader.mod.uk/staff/default.aspx")
    wait_for_aspx_load(page)
    wait_for_preloader(page)

    page.locator("[name='Staff_length']").select_option(value="-1")

    table = page.query_selector("#Staff tbody")
    if not table:
        raise Exception("Staff table not found")

    staff = []
    for row in table.query_selector_all("tr"):
        cols = row.query_selector_all("td")
        if len(cols) < 8:
            continue  # spacer/empty rows in the rendered table
        # Columns: 0 checkbox, 1 Firstname, 2 Surname, 3 Rank, 4 Birthday, 5 Age, 6 Gender, 7 CIN
        staff.append({
            "first_name": cols[1].inner_text().strip(),
            "last_name":  cols[2].inner_text().strip(),
            "rank":       cols[3].inner_text().strip(),
            "cin":        cols[7].inner_text().strip(),
        })
    return staff


def get_staff_address(page: Page):
    """Return the current address from a loaded staff profile, or None.

    Picks the row flagged 'Current'; falls back to the first address row. The
    address is the first non-empty text node of the cell (before the <br>/badge).
    """
    try:
        card = "//h4[normalize-space()='Address Details']/ancestor::div[contains(@class,'card')]"
        row = page.query_selector(
            f"xpath={card}//tbody//tr[.//span[contains(@class,'badge') and normalize-space()='Current']]"
        ) or page.query_selector(f"xpath={card}//tbody//tr[1]")
        if not row:
            return None
        td = row.query_selector("td")
        if not td:
            return None
        address = td.evaluate(
            "el => { for (const n of el.childNodes) { if (n.nodeType === 3) { const t = n.textContent.trim(); if (t) return t; } } return ''; }"
        )
        return address or None
    except Exception:
        return None


def add_staff_addresses(page, staff, scraper_messages, scraper_lock, stop_event=None):
    """Visit each staff member's profile (by row index) and set entry['address']."""
    total = len(staff)
    for i, entry in enumerate(staff):
        if stop_event and stop_event.is_set():
            return

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"Fetching address {i + 1} of {total}: {entry.get('first_name', '')} {entry.get('last_name', '')}".strip(),
            }))

        page.goto("https://sms.bader.mod.uk/staff/default.aspx")
        wait_for_aspx_load(page)
        page.locator("[name='Staff_length']").select_option(value="-1")
        wait_for_preloader(page)
        wait_for_aspx_load(page)

        link = page.wait_for_selector(
            f"#ctl00_ctl00_cphBaseBody_cphBody_lvStaff_ctrl{i}_lnkFamilyName",
            timeout=20000,
        )
        safe_click(page, link)
        wait_for_preloader(page)
        wait_for_aspx_load(page)

        entry["address"] = get_staff_address(page)


def attendance_periods(today):
    """(year, month) pairs to scrape: current year up to the current month, plus
    previous year's Jul–Dec while we're still in H1 — so the Jul–Dec HTD claim
    (filed in January) is available. Previous-year H1 is never pulled."""
    periods = [(today.year, m) for m in range(1, today.month + 1)]
    if today.month <= 6:
        periods = [(today.year - 1, m) for m in range(7, 13)] + periods
    return periods


def get_staff_attendance(page, scraper_messages, scraper_lock, stop_event=None):
    """Return {cin: {"YYYY-MM": PC+PI}} parade-night attendance per month.

    Covers a rolling two-half window (see attendance_periods), filtering the
    staff attendance register one month at a time.
    """
    today = date.today()

    page.goto("https://sms.bader.mod.uk/units/fields/attendance/staffAttendance.aspx")
    wait_for_aspx_load(page)
    wait_for_preloader(page)

    result = {}  # cin -> {month_key: count}
    for year, month in attendance_periods(today):
        if stop_event and stop_event.is_set():
            return result

        last_day = calendar.monthrange(year, month)[1]
        date_from = f"01/{month:02d}/{year}"
        date_to = f"{last_day:02d}/{month:02d}/{year}"
        month_key = f"{year}-{month:02d}"

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Fetching attendance for {month_key}..."}))

        page.fill("#ctl00_ctl00_cphBaseBody_cphBody_txtDateFrom", date_from)
        page.fill("#ctl00_ctl00_cphBaseBody_cphBody_txtDateTo", date_to)

        filter_btn = page.wait_for_selector("#ctl00_ctl00_cphBaseBody_cphBody_lbFilter", timeout=20000)
        safe_click(page, filter_btn)
        wait_for_preloader(page)
        wait_for_aspx_load(page)

        # Show all rows so DataTables client-side paging doesn't drop any from the DOM.
        try:
            page.locator("[name='staffAttendance_length']").select_option(value="100")
        except Exception:
            pass

        tbody = page.query_selector("#staffAttendance tbody")
        if not tbody:
            continue
        # Columns: 0 Personnel, 1 Service Number (CIN), 2 Register Type, 3 PC, 4 PI, ...
        for row in tbody.query_selector_all("tr"):
            cols = row.query_selector_all("td")
            if len(cols) < 5:
                continue
            cin = cols[1].inner_text().strip()
            if not cin:
                continue
            try:
                pc = int(cols[3].inner_text().strip() or 0)
                pi = int(cols[4].inner_text().strip() or 0)
            except ValueError:
                continue
            result.setdefault(cin, {})[month_key] = pc + pi

    return result


def staff_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_context_ready=None):
    context = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started staff scraper"}))

        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set(): return
        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return
        staff = get_staff(page)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {len(staff)} staff. Fetching addresses..."}))

        add_staff_addresses(page, staff, scraper_messages, scraper_lock, stop_event=stop_event)
        if stop_event.is_set(): return

        attendance_by_cin = get_staff_attendance(page, scraper_messages, scraper_lock, stop_event=stop_event)
        if stop_event.is_set(): return

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Matching emails..."}))

        try:
            workspace_users = get_workspace_users()
            email_map = {
                (u["first_name_key"], u["last_name_key"]): u["email"]
                for u in workspace_users
            }
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "info", "value": f"Fetched {len(workspace_users)} workspace accounts."}))
        except Exception as e:
            email_map = {}
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "warning", "value": f"[WARN] Could not fetch workspace emails: {str(e)}. Continuing without emails."}))

        if stop_event.is_set(): return

        saved = 0
        skipped = 0
        emails_matched = 0

        for entry in staff:
            cin = entry.get("cin")
            try:
                cin = int(cin)
            except (ValueError, TypeError):
                skipped += 1
                continue

            first_key = (entry.get("first_name") or "").strip().upper()
            last_key = (entry.get("last_name") or "").strip().upper()
            email = match_email(first_key, last_key, email_map)
            if email:
                emails_matched += 1

            member = db_session.query(Staff).filter(Staff.cin == cin).first()
            if not member:
                member = Staff(cin=cin)
                db_session.add(member)

            member.first_name = entry.get("first_name") or member.first_name
            member.last_name = entry.get("last_name") or member.last_name
            member.rank = entry.get("rank") or member.rank
            member.email = email or member.email
            member.address = entry.get("address") or member.address
            # Replace the whole map when the scrape produced data, so months we
            # no longer scrape (last year's dropped half) prune out; skip on an
            # empty scrape so a transient failure doesn't wipe everyone.
            if attendance_by_cin:
                member.attendance = attendance_by_cin.get(str(cin), {})
            saved += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"DB update complete — {saved} staff saved, {emails_matched} emails matched, {skipped} skipped."
            }))
            scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except PlaywrightTimeoutError:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "A page took too long to load (Timeout)."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Staff Scraper Error: {str(e)}"}))
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
