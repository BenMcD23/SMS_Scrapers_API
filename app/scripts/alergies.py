from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import json

from scripts.profiles import CADETS, open_profile
from scripts.tables import read_rows
from scripts.waiter import wait_for_aspx_load, wait_for_preloader


ALLERGIES_TABLE = "#ctl00_ctl00_cphBaseBody_cphBody_allergies_gvAllergies"
DIETARY_TABLE = "#ctl00_ctl00_cphBaseBody_cphBody_dietary_gvDietary"


def _read_allergy_rows(page: Page):
    """Allergy rows as (cell texts, auto-injector checked), in one round trip."""
    try:
        return page.evaluate(
            """(sel) => Array.from(document.querySelectorAll(sel + ' tbody tr')).map(tr => {
                const cells = Array.from(tr.querySelectorAll('td'));
                const box = cells[1] ? cells[1].querySelector('input') : null;
                return {cells: cells.map(td => td.innerText), injector: !!(box && box.checked)};
            })""",
            ALLERGIES_TABLE,
        )
    except Exception:
        return []


def _parse_allergy_rows(rows):
    allergies = []
    for row in rows:
        cells = row.get("cells") or []
        if len(cells) < 4 or not cells[0].strip():
            continue
        allergies.append({
            "allergy": cells[0].strip(),
            "auto_injector": "Yes" if row.get("injector") else "No",
            "severity": cells[2].strip(),
            "details": cells[3].strip(),
        })
    return allergies


def _parse_dietary_rows(rows):
    return [
        {"name": cells[0].strip(), "details": cells[1].strip()}
        for cells in rows
        if len(cells) >= 2 and cells[0].strip()
    ]


def get_cadet_medical(page: Page, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=None, profile_links=None):
    profile_links = profile_links or {}
    cadet_data = []
    fast_opens = 0

    def dbg(msg, stream=False):
        print(f"[MEDICAL DEBUG] {msg}")
        if stream and scraper_messages is not None and scraper_lock is not None:
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "info", "value": f"[debug] {msg}"}))

    for i in range(numberOfCadets):
        if stop_event and stop_event.is_set():
            return cadet_data

        cadet_name = cadetNames[i]

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Scraping cadet {i + 1}/{numberOfCadets}: {cadet_name}"}))

        dbg(f"--- cadet {i + 1}/{numberOfCadets}: {cadet_name} ---")

        if open_profile(page, i, profile_links, CADETS, numberOfCadets):
            fast_opens += 1

        # CIN
        cin = None
        try:
            cin_label = page.wait_for_selector(
                "#ctl00_ctl00_cphBaseBody_cphBody_overview_fvProfile_lblPersonnelNumber",
                timeout=20000,
            )
            cin_text = cin_label.evaluate(
                "el => { let sib = el.nextElementSibling; while(sib) { if(sib.tagName==='H6') return sib.innerText.trim(); sib=sib.nextElementSibling; } return ''; }"
            )
            cin = int(cin_text) if cin_text else None
        except Exception as e:
            dbg(f"  CIN extraction FAILED: {e}")
        dbg(f"  CIN = {cin}")

        # Medical tab
        try:
            medical_tab = page.wait_for_selector("xpath=//a[contains(text(), 'Medical')]", timeout=15000)
            medical_tab.evaluate("el => el.click()")
            wait_for_preloader(page)
            wait_for_aspx_load(page)
            dbg("  clicked 'Medical' tab")
        except Exception as e:
            dbg(f"  Medical tab click FAILED: {e}")

        # Allergies
        try:
            page.wait_for_selector(ALLERGIES_TABLE, timeout=15000)
        except Exception as e:
            dbg(f"  allergies table not found: {e}")

        # One evaluate rather than a CDP round trip per cell. The auto-injector
        # column is a checkbox, so its state comes back with the row texts.
        allergy_rows = _read_allergy_rows(page)
        dbg(f"  allergy rows found: {len(allergy_rows)}")

        allergies = _parse_allergy_rows(allergy_rows)
        for a in allergies:
            dbg(f"    allergy: {a['allergy']!r} | injector={a['auto_injector']} | severity={a['severity']!r} | details={a['details']!r}")
        dbg(f"  total allergies: {len(allergies)}")

        # Dietary restrictions
        dietary_rows = read_rows(page, DIETARY_TABLE)
        dbg(f"  dietary rows found: {len(dietary_rows)}")

        dietary_restrictions = _parse_dietary_rows(dietary_rows)
        for d in dietary_restrictions:
            dbg(f"    dietary: {d['name']!r} | details={d['details']!r}")
        dbg(f"  total dietary: {len(dietary_restrictions)}")

        cadet_data.append({
            "cin": cin,
            "cadet_name": cadet_name,
            "allergies": allergies,
            "dietary_restrictions": dietary_restrictions,
        })

    with scraper_lock:
        scraper_messages.append(json.dumps({
            "type": "info",
            "value": f"Opened {fast_opens} of {len(cadet_data)} profile(s) without redrawing the cadet list.",
        }))

    return cadet_data
