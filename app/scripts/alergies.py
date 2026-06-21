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

    for i in range(numberOfCadets):
        if i == 10:
            break
        if stop_event and stop_event.is_set():
            return cadet_data

        cadet_name = cadetNames[i]

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Scraping cadet {i + 1}/{numberOfCadets}: {cadet_name}"}))

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

        # Extract CIN
        cin = None
        try:
            cin_label = driver.find_element(
                By.ID, "ctl00_ctl00_cphBaseBody_cphBody_overview_fvProfile_lblPersonnelNumber"
            )
            cin_text = cin_label.find_element(By.XPATH, "following-sibling::h6[1]").text.strip()
            cin = int(cin_text) if cin_text else None
        except Exception:
            pass

        # Click Medical tab
        smart_click(driver, (By.XPATH, "//a[contains(text(), 'Medical')]"))

        # ------------------------------------------------
        # EXTRACT ALLERGIES
        # ------------------------------------------------
        allergy_rows = driver.find_elements(
            By.XPATH,
            "//div[@id='ctl00_ctl00_cphBaseBody_cphBody_allergies_upAlleriges']//table/tbody/tr"
        )

        allergies = []
        for row in allergy_rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 4:
                allergy_name = cols[0].text.strip()

                # Auto injector checkbox
                auto_injector = "Yes" if cols[1].find_element(By.TAG_NAME, "input").is_selected() else "No"

                severity = cols[2].text.strip()
                details = cols[3].text.strip()

                allergies.append({
                    "allergy": allergy_name,
                    "auto_injector": auto_injector,
                    "severity": severity,
                    "details": details
                })

        # ------------------------------------------------
        # EXTRACT DIETARY RESTRICTIONS
        # ------------------------------------------------
        dietary_rows = driver.find_elements(
            By.XPATH,
            "//div[@id='ctl00_ctl00_cphBaseBody_cphBody_dietary_upDietary']//table/tbody/tr"
        )

        dietary_restrictions = []
        for row in dietary_rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 2:
                dietary_restrictions.append({
                    "name": cols[0].text.strip(),
                    "details": cols[1].text.strip()
                })

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
