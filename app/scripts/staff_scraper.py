from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from datetime import date
import json

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click
from scripts.scraper_utils import init_scraper, login, match_email
from scripts.attendance import get_attendance

from database.models import Staff, StaffAttendance
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


def add_staff_profile_details(page, staff, scraper_messages, scraper_lock, stop_event=None):
    """Visit each staff member's profile (by row index) and set entry['address']
    and entry['attendance']."""
    total = len(staff)
    for i, entry in enumerate(staff):
        if stop_event and stop_event.is_set():
            return

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"Fetching profile {i + 1} of {total}: {entry.get('first_name', '')} {entry.get('last_name', '')}".strip(),
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
        entry["attendance"] = get_attendance(page)

def attendance_periods(today):
    """(year, month) pairs to scrape: current year up to the current month, plus
    previous year's Jul–Dec while we're still in H1 — so the Jul–Dec HTD claim
    (filed in January) is available. Previous-year H1 is never pulled."""
    periods = [(today.year, m) for m in range(1, today.month + 1)]
    if today.month <= 6:
        periods = [(today.year - 1, m) for m in range(7, 13)] + periods
    return periods


def monthly_attendance(records, today=None):
    """{"YYYY-MM": nights attended} over the HTD window (see attendance_periods).

    Derived from the profile's own attendance register, which replaced the
    month-by-month unit report scrape. Counts parade nights the person turned up
    to — dress state doesn't matter, so both "Present Correctly Dressed" and
    "Present Incorrectly Dressed" count, matching the report's old PC+PI sum.
    Every month in the window gets a key, at 0 if nothing was attended, so the
    HTD form always has a half to offer.
    """
    counts = {f"{y}-{m:02d}": 0 for y, m in attendance_periods(today or date.today())}
    for record in records:
        key = f"{record['date'].year}-{record['date'].month:02d}"
        if key not in counts:
            continue  # outside the claim window
        if (record.get("register_type") or "") != "Parade Night":
            continue
        if not (record.get("status") or "").startswith("Present"):
            continue
        counts[key] += 1
    return counts


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
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {len(staff)} staff. Fetching profiles..."}))

        add_staff_profile_details(page, staff, scraper_messages, scraper_lock, stop_event=stop_event)
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
        attendance_rows = 0

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

            # Bader's register is append-only, so replacing this person's rows
            # wholesale is always correct. Skipped on an empty scrape so a failed
            # tab load can't wipe existing history — which is also why the
            # monthly map is only rebuilt alongside the rows it's derived from.
            records = entry.get("attendance") or []
            if records:
                db_session.query(StaffAttendance).filter(
                    StaffAttendance.staff_id == cin
                ).delete(synchronize_session=False)
                db_session.add_all([
                    StaffAttendance(
                        staff_id=cin, date=a["date"], register_type=a["register_type"],
                        status=a["status"], unit=a["unit"],
                    )
                    for a in records
                ])
                attendance_rows += len(records)
                # Months outside the current window prune out, same as before.
                member.attendance = monthly_attendance(records)

            saved += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"DB update complete — {saved} staff saved, {emails_matched} emails matched, {skipped} skipped, {attendance_rows} attendance record(s)."
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
