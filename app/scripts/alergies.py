from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import json
import time


# ---------------------------------------------
# Helper functions
# ---------------------------------------------

LOADER = (By.CSS_SELECTOR, ".preloader")


def wait_for_loader(driver, timeout=20):
    """Wait until the loader overlay disappears."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located(LOADER)
        )
    except:
        pass


def smart_click(driver, locator, timeout=20):
    """Safe click using JS, with loader wait."""
    wait_for_loader(driver, timeout)

    element = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable(locator)
    )

    driver.execute_script("arguments[0].click();", element)
    wait_for_loader(driver, timeout)


# ---------------------------------------------
# MAIN SCRAPER FUNCTION
# ---------------------------------------------

def get_cadet_medical(driver, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=None):
    cadet_data = []

    def dbg(msg, stream=False):
        """Print to the server console (always) and optionally surface in the UI."""
        print(f"[MEDICAL DEBUG] {msg}")
        if stream and scraper_messages is not None and scraper_lock is not None:
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "info", "value": f"[debug] {msg}"}))

    for i in range(numberOfCadets):
        # if i == 10:
        #     dbg("Hit the i == 10 debug cap — only the first 10 cadets are scraped.")
        #     break
        if stop_event and stop_event.is_set():
            return cadet_data

        cadet_name = cadetNames[i]

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Scraping cadet {i + 1}/{numberOfCadets}: {cadet_name}"}))

        dbg(f"--- cadet {i + 1}/{numberOfCadets}: {cadet_name} ---")

        # Load cadet list page
        driver.get("https://sms.bader.mod.uk/cadets/default.aspx")
        wait_for_loader(driver)

        # Show all cadets
        Select(WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.NAME, 'Cadets_length'))
        )).select_by_value("-1")
        wait_for_loader(driver)

        # Navigate by index (same pattern as quali scraper)
        link = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.ID, f'ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{i}_lbFamilyName')
            )
        )
        driver.execute_script("arguments[0].click();", link)
        wait_for_loader(driver)

        # Extract CIN — wait for the profile sidebar to render. (wait_for_loader
        # only waits for the spinner; the postback content can lag behind it, so
        # find_element used to fire before lblPersonnelNumber existed → CIN None.)
        cin = None
        try:
            cin_label = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located(
                    (By.ID, "ctl00_ctl00_cphBaseBody_cphBody_overview_fvProfile_lblPersonnelNumber")))
            cin_text = cin_label.find_element(By.XPATH, "following-sibling::h6[1]").text.strip()
            cin = int(cin_text) if cin_text else None
        except Exception as e:
            dbg(f"  CIN extraction FAILED: {e}")
        dbg(f"  CIN = {cin}")

        # Click Medical tab
        try:
            smart_click(driver, (By.XPATH, "//a[contains(text(), 'Medical')]"))
            dbg("  clicked 'Medical' tab")
        except Exception as e:
            dbg(f"  Medical tab click FAILED: {e}")
        dbg(f"  current URL after Medical click: {driver.current_url}")

        # ------------------------------------------------
        # EXTRACT ALLERGIES
        # ------------------------------------------------
        # The allergies table is a DataTable (id ...allergies_gvAllergies) with a
        # real <thead>/<tbody>. Target it by id and read only its <tbody> rows.
        # Columns: [Allergy, Auto Injector (checkbox), Severity, Details, Options].
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.ID, "ctl00_ctl00_cphBaseBody_cphBody_allergies_gvAllergies")))
        except Exception as e:
            dbg(f"  allergies table not found: {e}")

        allergy_rows = driver.find_elements(
            By.CSS_SELECTOR,
            "#ctl00_ctl00_cphBaseBody_cphBody_allergies_gvAllergies tbody tr")
        dbg(f"  allergy rows found: {len(allergy_rows)}")

        allergies = []
        for row in allergy_rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) < 4:
                continue  # e.g. the DataTables "No data available" placeholder row
            try:
                allergy_name = cols[0].text.strip()
                if not allergy_name:
                    continue
                try:
                    auto_injector = "Yes" if cols[1].find_element(By.TAG_NAME, "input").is_selected() else "No"
                except Exception:
                    auto_injector = "No"
                severity = cols[2].text.strip()
                details = cols[3].text.strip()
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

        # ------------------------------------------------
        # EXTRACT DIETARY RESTRICTIONS
        # ------------------------------------------------
        # Columns: [Dietary Restriction (name), Details, Options]. Skip the
        # header row (its cells are <th>, so find_elements(td) is empty/short).
        dietary_rows = driver.find_elements(
            By.CSS_SELECTOR,
            "#ctl00_ctl00_cphBaseBody_cphBody_dietary_gvDietary tbody tr")
        dbg(f"  dietary rows found: {len(dietary_rows)}")

        dietary_restrictions = []
        for row in dietary_rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) < 2:
                continue
            name = cols[0].text.strip()
            if not name:
                continue
            details = cols[1].text.strip()
            dietary_restrictions.append({"name": name, "details": details})
            dbg(f"    dietary: {name!r} | details={details!r}")
        dbg(f"  total dietary: {len(dietary_restrictions)}")

        # ------------------------------------------------
        # STORE RESULT
        # ------------------------------------------------
        cadet_data.append({
            "cin": cin,
            "cadet_name": cadet_name,
            "allergies": allergies,
            "dietary_restrictions": dietary_restrictions
        })

        time.sleep(1)

    return cadet_data
