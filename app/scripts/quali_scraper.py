from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException

from datetime import datetime

import json
import time
import threading

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click
# Shared queue to store scraper messages
scraper_lock = threading.Lock()

def get_cadet_names(driver):
    driver.get("https://sms.bader.mod.uk/cadets/default.aspx")

    wait_for_aspx_load(driver)

    select = Select(WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.NAME, 'Cadets_length'))))
    select.select_by_value('-1')

    cadetNames = []
    table = driver.find_elements(By.XPATH, '//*/tbody')
    if not table:
        raise Exception("Cadet table not found")
    rows = table[0].find_elements(By.TAG_NAME, "tr")
    if not rows:
        raise Exception("No cadet rows found")

    for row in rows:
        columns = row.find_elements(By.TAG_NAME, 'td')
        name = " ".join((columns[i].text).replace("\n", " ") for i in [1, 2])
        cadetNames.append(name.strip())

    info_text = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.ID, 'Cadets_info'))
    ).text

    try:
        numberOfCadets = int(info_text.split(" ")[5])
    except (IndexError, ValueError):
        raise Exception(f"Failed to parse number of cadets from text: '{info_text}'")

    return cadetNames, numberOfCadets


# Classification levels, highest first. Each maps the classification label to the
# "result type" input on the Classification → Summary panel, which reads "Pass"
# once achieved. "Basic Cadet Part 3" is the First Class pass (Parts 1 & 2 ignored).
CLASSIFICATION_LEVELS = [
    ("Master Air Cadet",  "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_StaffCadetPart1ResultTypeLabel"),
    ("Senior Cadet",      "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_SeniorCadetResultTypeLabel"),
    ("Leading Cadet",     "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_LeadingCadetResultTypeLabel"),
    ("First Class Cadet", "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_FirstClassPart3TypeLabel"),
]


def get_classification(driver):
    """Open the cadet's Classification → Summary panel and return the highest
    classification they've passed (or "Junior Cadet" if none). Returns None on
    failure so the rest of the scrape isn't lost."""
    try:
        # Same click-through pattern as the qualifications tabs above: open the
        # "Classification" tab (a Bootstrap dropdown-toggle), then its "Summary"
        # item. Matched by EXACT tab id, not link text — the sidebar's
        # "Classification" report link has identical text and sits earlier in the
        # DOM, so a text match navigates to /reports instead.
        class_tab_ids = [
            "ctl00_ctl00_cphBaseBody_cphBody_TabsCadet1_Classification",
            "ctl00_ctl00_cphBaseBody_cphBody_TabsCadet1_Summary",
        ]
        for elem_id in class_tab_ids:
            tab_element = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, elem_id)))
            safe_click(driver, tab_element)
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)
            time.sleep(0.5)

        # Wait for the classification panel to render before reading the rows.
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.ID, "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_lbEdit")))

        for label, input_id in CLASSIFICATION_LEVELS:
            try:
                value = driver.find_element(By.ID, input_id).get_attribute("value")
            except Exception:
                continue
            if value and value.strip().lower() == "pass":
                return label
        return "Junior Cadet"

    except Exception as e:
        print(f"Warning: Could not extract classification: {e}")
        return None


def get_cadet_info_and_qualifications(driver, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=None):
    cadet_data = []

    for i in range(numberOfCadets):
        if stop_event and stop_event.is_set():
            return cadet_data

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Scraping cadet {i + 1} of {numberOfCadets}: {cadetNames[i]}"}))

        
        # if i == 5:
        #     break

        driver.get("https://sms.bader.mod.uk/cadets/default.aspx")
        wait_for_aspx_load(driver)

        Select(WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.NAME, 'Cadets_length')))).select_by_value('-1')

        wait_for_preloader(driver)
        wait_for_aspx_load(driver)

        link = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, f'ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{i}_lbFamilyName')))
        safe_click(driver, link)

        wait_for_preloader(driver)
        wait_for_aspx_load(driver)

        # ── Scrape profile sidebar ────────────────────────────────────────────

        # CIN — the <h6> immediately after the lblPersonnelNumber <p>
        try:
            cin_label = driver.find_element(By.ID, "ctl00_ctl00_cphBaseBody_cphBody_overview_fvProfile_lblPersonnelNumber")
            cin = cin_label.find_element(By.XPATH, "following-sibling::h6[1]").text.strip()
        except Exception:
            cin = None

        # Rank — the card-subtitle on the profile sidebar
        try:
            rank = driver.find_element(By.CLASS_NAME, "card-subtitle").text.strip()
        except Exception:
            rank = None

        # ── Scrape personal details form ──────────────────────────────────────

        # First name
        try:
            first_name = driver.find_element(
                By.ID, "ctl00_ctl00_cphBaseBody_cphBody_fvCadetDetail_txtGivenName"
            ).get_attribute("value").strip()
        except Exception:
            first_name = cadetNames[i].split()[0] if cadetNames[i] else None

        # Last name
        try:
            last_name = driver.find_element(
                By.ID, "ctl00_ctl00_cphBaseBody_cphBody_fvCadetDetail_txtSurname"
            ).get_attribute("value").strip()
        except Exception:
            last_name = cadetNames[i].split()[-1] if cadetNames[i] else None

        # Date of Birth — disabled input with label "Date of Birth"
        try:
            dob_input = driver.find_element(
                By.XPATH,
                "//label[normalize-space()='Date of Birth']/following-sibling::input[@type='text'][1] | "
                "//label[normalize-space()='Date of Birth']/../following-sibling::div//input[@type='text'][1]"
            )
            dob_str = dob_input.get_attribute("value").strip()   # "28/04/2010"
            date_of_birth = datetime.strptime(dob_str, "%d/%m/%Y") if dob_str else None
        except Exception:
            date_of_birth = None

        # Flight — selected option in the flight dropdown
        try:
            flight_select = Select(driver.find_element(
                By.ID, "ctl00_ctl00_cphBaseBody_cphBody_fvCadetDetail_ddlFlightEdit"
            ))
            flight = flight_select.first_selected_option.text.strip()
            if flight == "Please Select ...":
                flight = None
        except Exception:
            flight = None

        # ── Classification (Classification → Summary tab) ─────────────────────
        # Done BEFORE the qualifications tabs: navigating to General Qualifications
        # leaves the cadet profile (its own page), so the TabsCadet1 bar — and the
        # Classification tab with it — is no longer in the DOM afterwards.
        classification = get_classification(driver)

        # ── Navigate to qualifications tabs ──────────────────────────────────

        tabs = [
            "//a[contains(text(), 'Qualifications & Awards')]",
            "//a[contains(text(), 'General Qualifications')]"
        ]

        for tab_xpath in tabs:
            tab_element = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, tab_xpath)))
            safe_click(driver, tab_element)
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)
            time.sleep(0.5)

        wait_for_aspx_load(driver)

        cadetQualifications = []
        try:
            wait_for_aspx_load(driver)
            table = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//*/tbody')))
            rows = table.find_elements(By.TAG_NAME, "tr")

            for row in rows:
                cols = row.find_elements(By.TAG_NAME, 'td')
                if not cols or not cols[0].text.strip():
                    continue

                qual_type = cols[0].text.replace("\n", " ").strip()

                # Date achieved — col 1
                date_achieved = None
                if len(cols) > 1:
                    try:
                        date_achieved = datetime.strptime(cols[1].text.strip(), "%d/%m/%Y")
                    except (ValueError, IndexError):
                        pass

                # Date expires — col 2 ("N/A" means no expiry)
                date_expires = None
                if len(cols) > 2:
                    try:
                        date_expires = datetime.strptime(cols[2].text.strip(), "%d/%m/%Y")
                    except (ValueError, IndexError):
                        pass  # "N/A" or empty — leave as None

                cadetQualifications.append({
                    "qual_type":     qual_type,
                    "status":        "true",
                    "date_achieved": date_achieved,
                    "date_expires":  date_expires,
                })

        except Exception as e:
            print(f"Warning: Could not extract qualifications for {cadetNames[i]}: {e}")

        cadet_data.append({
            "cin":           cin,
            "first_name":    first_name,
            "last_name":     last_name,
            "rank":          rank,
            "flight":        flight,
            "date_of_birth": date_of_birth,
            "classification": classification,
            "qualifications": cadetQualifications,
        })

    return cadet_data