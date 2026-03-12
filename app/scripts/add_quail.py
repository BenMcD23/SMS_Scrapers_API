import time
import os

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click


def add_qualification_with_attachment(
    driver,
    cadet_cin: int | str,
    qualification_name: str,
    pdf_path: str,
    scraper_messages=None,
    scraper_lock=None,
):
    """
    Navigate to a cadet's General Qualifications tab, add a named qualification,
    then upload a PDF proof labelled "Assessment Sheet".

    Parameters
    ----------
    driver            : Selenium WebDriver
    cadet_cin         : The cadet's CIN (used to find them in the list)
    qualification_name: Exact or partial name of the qualification to select
                        (matched against the dropdown options, case-insensitive)
    pdf_path          : Absolute path to the PDF file to upload
    scraper_messages  : Optional shared list for status messages
    scraper_lock      : Optional threading lock for scraper_messages
    """

    def log(msg):
        if scraper_messages is not None and scraper_lock is not None:
            with scraper_lock:
                scraper_messages.append(msg)
        else:
            print(msg)

    pdf_path = os.path.abspath(pdf_path)
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    cin_str = str(cadet_cin).strip()

    # ── 1. Open cadets list and find the cadet by CIN ────────────────────────
    log(f"Navigating to cadet list to find CIN {cin_str}...")
    driver.get("https://sms.bader.mod.uk/cadets/default.aspx")
    wait_for_aspx_load(driver)

    # Show all cadets
    Select(
        WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.NAME, "Cadets_length"))
        )
    ).select_by_value("-1")

    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    # Find the row whose last <td> (CIN column) matches our CIN, then click
    # the family-name link in that row to open the cadet profile.
    rows = WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located((By.XPATH, "//tbody/tr"))
    )

    cadet_index = None
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if cols and cols[-1].text.strip() == cin_str:
            # The family-name link id encodes the row index
            link = row.find_element(By.XPATH, ".//a[contains(@id,'lbFamilyName')]")
            link_id = link.get_attribute("id")
            # e.g. ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl5_lbFamilyName
            cadet_index = int(link_id.split("_ctrl")[1].split("_")[0])
            break

    if cadet_index is None:
        raise ValueError(f"Cadet with CIN {cin_str} not found in the cadets list.")

    log(f"Found cadet at list index {cadet_index}. Opening profile...")
    link = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable(
            (By.ID, f"ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{cadet_index}_lbFamilyName")
        )
    )
    safe_click(driver, link)
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    # ── 2. Navigate to Qualifications & Awards → General Qualifications ───────
    log("Navigating to General Qualifications tab...")
    for tab_text in ["Qualifications & Awards", "General Qualifications"]:
        tab = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, f"//a[contains(text(), '{tab_text}')]")
            )
        )
        safe_click(driver, tab)
        wait_for_preloader(driver)
        wait_for_aspx_load(driver)
        time.sleep(0.5)

    # ── 3. Click "Add Qualification" button ───────────────────────────────────
    log("Clicking 'Add Qualification'...")
    add_qual_btn = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.ID, "ctl00_ctl00_cphBaseBody_cphBody_lbBulkAddQuals"))
    )
    safe_click(driver, add_qual_btn)
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)
    time.sleep(0.5)

    # ── 4. Select qualification in the modal dropdown ─────────────────────────
    log(f"Selecting qualification: {qualification_name}...")
    qual_select_el = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable(
            (By.ID, "ctl00_ctl00_cphBaseBody_cphBody_bulkUpdateQualifications_fvBulkAddQualifications_ddlValidQualifications")
        )
    )
    qual_select = Select(qual_select_el)

    # Try exact match first, then case-insensitive partial match
    target = qualification_name.strip().lower()
    matched_value = None
    for option in qual_select.options:
        if option.text.strip().lower() == target:
            matched_value = option.get_attribute("value")
            break
    if matched_value is None:
        for option in qual_select.options:
            if target in option.text.strip().lower():
                matched_value = option.get_attribute("value")
                log(f"Partial match found: '{option.text.strip()}'")
                break
    if matched_value is None:
        raise ValueError(
            f"Qualification '{qualification_name}' not found in the dropdown. "
            "Check the exact name against the available options."
        )

    qual_select.select_by_value(matched_value)
    time.sleep(0.3)

    # ── 5. Submit the modal (find and click the Save/Add button inside it) ────
    log("Submitting the Add Qualification modal...")
    # The modal save button — look for a button/link with "Save" or "Add" inside the modal
    save_btn = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable(
            (By.XPATH,
             "//div[contains(@class,'modal show')]//a[contains(@class,'btn') and "
             "(contains(translate(text(),'SAVE','save'),'save') or "
             " contains(translate(text(),'ADD','add'),'add'))] | "
             "//div[contains(@class,'modal show')]//input[@type='submit']")
        )
    )
    safe_click(driver, save_btn)
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)
    time.sleep(1)

    # ── 6. Find the newly-added qualification row ─────────────────────────────
    log(f"Locating the '{qualification_name}' row in the qualifications list...")

    # Rows are inside lvQualifications repeater — find the one whose text matches
    qual_rows = WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located(
            (By.XPATH, "//div[contains(@id,'lvQualifications')]//div[contains(@class,'card')]")
        )
    )

    qual_row = None
    for row in qual_rows:
        if target in row.text.strip().lower():
            qual_row = row
            break

    if qual_row is None:
        # Fall back: just take the first row (most recently added)
        log("Warning: could not match qualification row by name — using first row.")
        qual_row = qual_rows[0]

    # ── 7. Click "Attachments" (expand/toggle) ───────────────────────────────
    log("Opening Attachments panel...")
    attachments_toggle = WebDriverWait(qual_row, 10).until(
        EC.element_to_be_clickable(
            (By.XPATH, ".//a[contains(translate(text(),'ATTACHMENTS','attachments'),'attachment')]"
                       " | .//button[contains(translate(text(),'ATTACHMENTS','attachments'),'attachment')]"
                       " | .//a[contains(@id,'Proof') or contains(@id,'proof') or contains(@id,'Attach')]")
        )
    )
    safe_click(driver, attachments_toggle)
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)
    time.sleep(0.5)

    # ── 8. Upload the PDF via the file input ──────────────────────────────────
    log(f"Uploading PDF: {pdf_path}...")
    # The file input may have a dynamic ctrl index — find it within the qual row
    file_input = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located(
            (By.XPATH,
             "//input[@type='file' and contains(@id,'AttachmentFileUpload')]")
        )
    )
    file_input.send_keys(pdf_path)
    time.sleep(0.5)

    # ── 9. Enter description "Assessment Sheet" ───────────────────────────────
    log("Entering description 'Assessment Sheet'...")
    desc_input = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located(
            (By.XPATH,
             "//input[@type='text' and contains(@id,'txtFooterDescription')]")
        )
    )
    desc_input.clear()
    desc_input.send_keys("Assessment Sheet")
    time.sleep(0.3)

    # ── 10. Click "Add" to save the attachment ────────────────────────────────
    log("Clicking Add to save the attachment...")
    add_attachment_btn = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable(
            (By.XPATH,
             "//a[contains(@id,'lbAddQualificationProof')]")
        )
    )
    safe_click(driver, add_attachment_btn)
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)
    time.sleep(1)

    log(f"Done — qualification '{qualification_name}' with PDF attachment saved for CIN {cin_str}.")