from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import json
import time

from scripts.waiter import wait_for_preloader


def get_cadet_medical(page: Page, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=None):
    cadet_data = []

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

        page.goto("https://sms.bader.mod.uk/cadets/default.aspx")
        wait_for_preloader(page)

        page.locator("[name='Cadets_length']").select_option(value="-1")
        wait_for_preloader(page)

        link = page.wait_for_selector(
            f"#ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{i}_lbFamilyName",
            timeout=20000,
        )
        link.evaluate("el => el.click()")
        wait_for_preloader(page)

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
            dbg("  clicked 'Medical' tab")
        except Exception as e:
            dbg(f"  Medical tab click FAILED: {e}")

        # Allergies
        try:
            page.wait_for_selector(
                "#ctl00_ctl00_cphBaseBody_cphBody_allergies_gvAllergies",
                timeout=15000,
            )
        except Exception as e:
            dbg(f"  allergies table not found: {e}")

        allergy_rows = page.query_selector_all(
            "#ctl00_ctl00_cphBaseBody_cphBody_allergies_gvAllergies tbody tr"
        )
        dbg(f"  allergy rows found: {len(allergy_rows)}")

        allergies = []
        for row in allergy_rows:
            cols = row.query_selector_all("td")
            if len(cols) < 4:
                continue
            try:
                allergy_name = cols[0].inner_text().strip()
                if not allergy_name:
                    continue
                try:
                    checkbox = cols[1].query_selector("input")
                    auto_injector = "Yes" if (checkbox and checkbox.is_checked()) else "No"
                except Exception:
                    auto_injector = "No"
                severity = cols[2].inner_text().strip()
                details = cols[3].inner_text().strip()
                allergies.append({
                    "allergy": allergy_name,
                    "auto_injector": auto_injector,
                    "severity": severity,
                    "details": details,
                })
                dbg(f"    allergy: {allergy_name!r} | injector={auto_injector} | severity={severity!r} | details={details!r}")
            except Exception as e:
                dbg(f"    allergy row parse failed: {e}")
        dbg(f"  total allergies: {len(allergies)}")

        # Dietary restrictions
        dietary_rows = page.query_selector_all(
            "#ctl00_ctl00_cphBaseBody_cphBody_dietary_gvDietary tbody tr"
        )
        dbg(f"  dietary rows found: {len(dietary_rows)}")

        dietary_restrictions = []
        for row in dietary_rows:
            cols = row.query_selector_all("td")
            if len(cols) < 2:
                continue
            name = cols[0].inner_text().strip()
            if not name:
                continue
            details = cols[1].inner_text().strip()
            dietary_restrictions.append({"name": name, "details": details})
            dbg(f"    dietary: {name!r} | details={details!r}")
        dbg(f"  total dietary: {len(dietary_restrictions)}")

        cadet_data.append({
            "cin": cin,
            "cadet_name": cadet_name,
            "allergies": allergies,
            "dietary_restrictions": dietary_restrictions,
        })

        time.sleep(1)

    return cadet_data
