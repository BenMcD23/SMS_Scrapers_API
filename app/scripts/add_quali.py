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
    pdf_paths: list[str],
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

    cin_str = str(cadet_cin).strip()
    print("here")
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

    # ── 6. Refresh and re-navigate to General Qualifications ─────────────────
    log("Refreshing page to load qualification row...")
    driver.refresh()
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)
    time.sleep(1)

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

    # ── 7. Find the ctrl index for our qualification ──────────────────────────
    # Each qual row is a <tr> whose first <td> text matches the qual name.
    # The sibling row's inputs encode the ctrl index, e.g. lvQualifications_ctrl0_...
    log(f"Finding ctrl index for '{qualification_name}'...")

    target = qualification_name.strip().lower()

    qual_table_rows = WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located(
            (By.XPATH, "//table[contains(@id,'Qualification') or @class[contains(.,'tablelist')]]//tr[not(contains(@class,'sibling')) and td]")
        )
    )

    ctrl_index = None
    for i, row in enumerate(qual_table_rows):
        cells = row.find_elements(By.TAG_NAME, "td")
        if cells and target in cells[0].text.strip().lower():
            ctrl_index = i
            break

    if ctrl_index is None:
        # Fallback: scan edit link IDs to find the right ctrl number
        edit_links = driver.find_elements(
            By.XPATH, "//a[contains(@id,'lvQualifications_ctrl') and contains(@id,'_lbEdit')]"
        )
        for link in edit_links:
            link_id = link.get_attribute("id")
            # Walk up to the parent <tr> and check its text
            parent_tr = link.find_element(By.XPATH, "./ancestor::tr[1]")
            if target in parent_tr.text.strip().lower():
                ctrl_index = int(link_id.split("_ctrl")[1].split("_")[0])
                break

    if ctrl_index is None:
        raise ValueError(f"Could not find qualification row for '{qualification_name}' after refresh.")

    log(f"Found qualification at ctrl index {ctrl_index}.")

    # ── 8. Show the sibling row via JS (toggleSibling has no ID to click) ────
    log("Revealing attachments panel via JavaScript...")
    sibling_rows = driver.find_elements(By.XPATH, "//tr[contains(@class,'sibling')]")

    if ctrl_index >= len(sibling_rows):
        raise ValueError(
            f"Sibling row index {ctrl_index} out of range (only {len(sibling_rows)} sibling rows found)."
        )

    sibling_row = sibling_rows[ctrl_index]
    driver.execute_script("arguments[0].style.display = 'table-row';", sibling_row)
    time.sleep(0.5)

# ── 9. Upload each PDF one at a time ─────────────────────────────────────
    for i, path in enumerate(pdf_paths):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"PDF not found: {path}")

        # Build clean names for this PDF
        safe_qual_name = qualification_name.replace(" ", "_")
        new_filename = f"{safe_qual_name}_Assessment_{i + 1}.pdf"
        description = f"Assessment Sheet {i + 1}"

        log(f"Uploading PDF {i+1}/{len(pdf_paths)} as '{new_filename}'...")

        # Re-show sibling row
        sibling_rows = driver.find_elements(By.XPATH, "//tr[contains(@class,'sibling')]")
        sibling_row = sibling_rows[ctrl_index]
        driver.execute_script("arguments[0].style.display = 'table-row';", sibling_row)
        time.sleep(0.5)

        ctrl_prefix = (
            f"ctl00_ctl00_cphBaseBody_cphBody_lvQualifications_ctrl{ctrl_index}"
            f"_gvQualificationProofs"
        )

        # Rename the temp file to the clean name before sending —
        # the browser uses the actual filename on disk as the upload name
        renamed_path = os.path.join(os.path.dirname(path), new_filename)
        os.rename(path, renamed_path)

        file_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH,
                 f"//input[@type='file' and contains(@id,'{ctrl_prefix}') "
                 f"and contains(@id,'AttachmentFileUpload')]")
            )
        )
        file_input.send_keys(renamed_path)
        time.sleep(0.5)

        desc_input = driver.find_element(
            By.XPATH,
            f"//input[@type='text' and contains(@id,'{ctrl_prefix}') "
            f"and (contains(@id,'txtEmptyInsertDescription') or contains(@id,'txtFooterDescription'))]"
        )
        desc_input.clear()
        desc_input.send_keys(description)
        time.sleep(0.3)

        add_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 f"//a[contains(@id,'{ctrl_prefix}') "
                 f"and (contains(@id,'lbAddEmptyInsert') or contains(@id,'lbAddFooterInsert') or contains(@id,'lbAddQualificationProof'))]")
            )
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", add_btn)
        wait_for_preloader(driver)
        wait_for_aspx_load(driver)
        time.sleep(1)

        log(f"✓ PDF {i+1} attached as '{new_filename}'.")

    log(f"Done — qualification '{qualification_name}' with {len(pdf_paths)} PDF(s) saved for CIN {cin_str}.")
