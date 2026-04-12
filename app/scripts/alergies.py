from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
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
        if stop_event and stop_event.is_set():
            return cadet_data

        cadet_name = cadetNames[i]

        # Thread-safe status update
        with scraper_lock:
            scraper_messages.append(f"Scraping cadet {i + 1}/{numberOfCadets}: {cadet_name}")
            print(f"[Scraper] {scraper_messages[-1]}")

        # Load cadet list page
        driver.get("https://sms.bader.mod.uk/cadets/default.aspx")
        wait_for_loader(driver)

        # Show all cadets
        Select(WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.NAME, 'Cadets_length'))
        )).select_by_value("-1")
        wait_for_loader(driver)

        # Split cadet name
        first, last = cadet_name.split()

        # Find cadet row
        row_xpath = f"""
        //tr[
            td/a[text()='{first}'] and
            td/a[text()='{last}']
        ]
        """

        row = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, row_xpath))
        )
        wait_for_loader(driver)

        # Click Family Name link
        family_link = row.find_element(By.XPATH, ".//a[contains(@id, 'lbFamilyName')]")
        driver.execute_script("arguments[0].click();", family_link)
        wait_for_loader(driver)

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
        # -------------------   -----------------------------
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
                    "name": cols[0].text.strip(),      # e.g., "Other", "Vegetarian"
                    "details": cols[1].text.strip()    # e.g., "Gluten Free"
                })
        # ------------------------------------------------
        # STORE RESULT
        # ------------------------------------------------
        cadet_data.append({
            "cadet_name": cadet_name,
            "allergies": allergies,
            "dietary_restrictions": dietary_restrictions
        })

        time.sleep(1)

    return cadet_data
