from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException

from datetime import datetime

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

def get_cadet_info_and_qualifications(driver, cadetNames, numberOfCadets, scraper_messages, scraper_lock):
    cadet_data = []

    for i in range(numberOfCadets):
        with scraper_lock:
            scraper_messages.append(f"Scraping cadet {i + 1} of {numberOfCadets}: {cadetNames[i]}")
        
        if i == 5:
            break

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
                if cols and cols[0].text.strip():
                    cadetQualifications.append(cols[0].text.replace("\n", " ").strip())
        except Exception as e:
            print(f"Warning: Could not extract qualifications for {cadetNames[i]}: {e}")

        cadet_data.append({
            "cin":            cin,
            "first_name":     first_name,
            "last_name":      last_name,
            "rank":           rank,
            "flight":         flight,
            "date_of_birth":  date_of_birth,
            "qualifications": cadetQualifications,
        })

    return cadet_data