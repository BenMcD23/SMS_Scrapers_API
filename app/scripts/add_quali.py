import time
import os
import json

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click


def add_qualification_with_attachment(
    page: Page,
    cadet_cin: int | str,
    qualification_name: str,
    pdf_paths: list[str],
    award_date: str | None = None,
    qualification_id: int | str | None = None,
    attachment_label: str = "Assessment Sheet",
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
                        (matched against the dropdown options, case-insensitive).
                        Also used to find the qualification's row after saving.
    qualification_id  : Bader dropdown <option value> for the qualification (from
                        core.qualifications). When given, the dropdown is selected
                        by value directly — robust against text variations — and
                        the name is only used to relocate the row afterwards.
    pdf_path          : Absolute path to the PDF file to upload
    scraper_messages  : Optional shared list for status messages
    scraper_lock      : Optional threading lock for scraper_messages
    """

    def log(msg, level="info"):
        print(f"[add_quali] {level}: {msg}", flush=True)
        payload = json.dumps({"type": level, "value": msg})
        if scraper_messages is not None and scraper_lock is not None:
            with scraper_lock:
                scraper_messages.append(payload)

    cin_str = str(cadet_cin).strip()

    # 1. Open cadets list and find the cadet by CIN
    log(f"Navigating to cadet list to find CIN {cin_str}...")
    page.goto("https://sms.bader.mod.uk/cadets/default.aspx")
    wait_for_aspx_load(page)

    page.locator("[name='Cadets_length']").select_option(value="-1")
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    rows = page.query_selector_all("xpath=//tbody/tr")

    cadet_index = None
    for row in rows:
        cols = row.query_selector_all("td")
        if cols and cols[-1].inner_text().strip() == cin_str:
            link = row.query_selector("xpath=.//a[contains(@id,'lbFamilyName')]")
            link_id = link.get_attribute("id")
            cadet_index = int(link_id.split("_ctrl")[1].split("_")[0])
            break

    if cadet_index is None:
        raise ValueError(f"Cadet with CIN {cin_str} not found in the cadets list.")

    log(f"Found cadet at list index {cadet_index}. Opening profile...")
    link = page.wait_for_selector(
        f"#ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{cadet_index}_lbFamilyName",
        timeout=20000,
    )
    safe_click(page, link)
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    # 2. Navigate to Qualifications & Awards -> General Qualifications
    log("Navigating to General Qualifications tab...")
    for tab_text in ["Qualifications & Awards", "General Qualifications"]:
        tab = page.wait_for_selector(
            f"xpath=//a[contains(text(), '{tab_text}')]",
            timeout=15000,
        )
        safe_click(page, tab)
        wait_for_preloader(page)
        wait_for_aspx_load(page)
        time.sleep(0.5)

    # 3. Click "Add Qualification" button
    log("Clicking 'Add Qualification'...")
    add_qual_btn = page.wait_for_selector(
        "#ctl00_ctl00_cphBaseBody_cphBody_lbBulkAddQuals",
        timeout=15000,
    )
    safe_click(page, add_qual_btn)
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    time.sleep(0.5)

    # 4. Select qualification in the modal dropdown
    log(f"Selecting qualification: {qualification_name}...")
    qual_select_id = (
        "#ctl00_ctl00_cphBaseBody_cphBody_bulkUpdateQualifications_"
        "fvBulkAddQualifications_ddlValidQualifications"
    )
    qual_select_el = page.wait_for_selector(qual_select_id, timeout=15000)

    matched_value = None
    # Preferred: select by the known Bader option id (from core.qualifications).
    if qualification_id is not None:
        wanted = str(qualification_id)
        if any(o.get_attribute("value") == wanted for o in qual_select.options):
            matched_value = wanted
        else:
            log(f"Option id {wanted} not in dropdown — falling back to name match.", "warning")

    # Fallback: exact match first, then case-insensitive partial match.
    if matched_value is None:
        target = qualification_name.strip().lower()
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
            f"Qualification '{qualification_name}' not found in the dropdown."
        )

    qual_select_el.select_option(value=matched_value)
    time.sleep(0.3)

    # 4b. Set Date Awarded
    if award_date:
        log(f"Setting date awarded to {award_date}...")
        date_input_id = (
            "#ctl00_ctl00_cphBaseBody_cphBody_bulkUpdateQualifications_"
            "fvBulkAddQualifications_txtDateAwarded"
        )
        date_input = page.wait_for_selector(date_input_id, timeout=10000)
        date_input.click()
        date_input.press("Control+a")
        date_input.press("Delete")
        date_input.type(award_date)
        time.sleep(0.2)
        date_input.press("Tab")
        time.sleep(0.2)

        date_input.evaluate(
            """(el, val) => {
                if (window.jQuery) { try { jQuery(el).datetimepicker('date', val); } catch(e) {} }
                if (el.value !== val) { el.value = val; }
                if (window.jQuery) { jQuery(el).trigger('change'); }
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            award_date,
        )
        time.sleep(0.2)

        actual = date_input.input_value()
        if actual != award_date:
            log(f"[WARN] Date field shows '{actual}' after setting '{award_date}'.", "warning")

    # 5. Submit the modal
    log("Submitting the Add Qualification modal...")
    save_btn = None
    for xpath in [
        "//div[contains(@class,'modal show')]//a[contains(@class,'btn') and (contains(translate(text(),'SAVE','save'),'save') or contains(translate(text(),'ADD','add'),'add'))]",
        "//div[contains(@class,'modal show')]//input[@type='submit']",
        "//a[contains(@class,'btn') and contains(translate(text(),'SAVE','save'),'save')]",
    ]:
        try:
            save_btn = page.wait_for_selector(f"xpath={xpath}", timeout=5000)
            if save_btn:
                break
        except PlaywrightTimeoutError:
            continue

    if save_btn is None:
        raise Exception("Could not find save button in Add Qualification modal.")

    safe_click(page, save_btn)
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    time.sleep(1)

    # 6. Refresh and re-navigate to General Qualifications
    log("Refreshing page to load qualification row...")
    page.reload()
    wait_for_preloader(page)
    wait_for_aspx_load(page)
    time.sleep(1)

    for tab_text in ["Qualifications & Awards", "General Qualifications"]:
        tab = page.wait_for_selector(
            f"xpath=//a[contains(text(), '{tab_text}')]",
            timeout=15000,
        )
        safe_click(page, tab)
        wait_for_preloader(page)
        wait_for_aspx_load(page)
        time.sleep(0.5)

    # 7. Find ctrl index for our qualification
    log(f"Finding ctrl index for '{qualification_name}'...")
    target = qualification_name.strip().lower()

    edit_links = page.query_selector_all(
        "xpath=//a[contains(@id,'lvQualifications_ctrl') and contains(@id,'_lbEdit')]"
    )

    ctrl_index = None
    for lnk in edit_links:
        parent_tr = lnk.evaluate_handle("el => el.closest('tr')")
        parent_text = parent_tr.evaluate("el => el.innerText").lower()
        if target in parent_text:
            ctrl_index = int(lnk.get_attribute("id").split("_ctrl")[1].split("_")[0])
            break

    if ctrl_index is None:
        raise ValueError(f"Could not find qualification row for '{qualification_name}' after refresh.")

    log(f"Found qualification at ctrl index {ctrl_index}.")

    # 8. Show the sibling (attachments) row via JS
    def reveal_sibling_row():
        matches = page.query_selector_all(
            f"xpath=//tr[contains(@class,'sibling')][.//*[contains(@id,'lvQualifications_ctrl{ctrl_index}_')]]"
        )
        if not matches:
            raise ValueError(f"Could not find attachments (sibling) row for ctrl index {ctrl_index}.")
        matches[0].evaluate("el => el.style.display = 'table-row'")
        return matches[0]

    log("Revealing attachments panel via JavaScript...")
    reveal_sibling_row()
    time.sleep(0.5)

    # 9. Upload each PDF one at a time
    for i, path in enumerate(pdf_paths):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"PDF not found: {path}")

        safe_qual_name = qualification_name.replace(" ", "_")
        new_filename = f"{safe_qual_name}_Assessment_{i + 1}.pdf"
        suffix = f" {i + 1}" if len(pdf_paths) > 1 else ""
        description = f"{attachment_label}{suffix}"

        log(f"Uploading PDF {i+1}/{len(pdf_paths)} as '{new_filename}'...")

        reveal_sibling_row()
        time.sleep(0.5)

        ctrl_prefix = (
            f"ctl00_ctl00_cphBaseBody_cphBody_lvQualifications_ctrl{ctrl_index}"
            f"_gvQualificationProofs"
        )

        renamed_path = os.path.join(os.path.dirname(path), new_filename)
        os.rename(path, renamed_path)

        # Playwright set_input_files works even on hidden file inputs
        file_input = page.query_selector(
            f"xpath=//input[@type='file' and contains(@id,'{ctrl_prefix}') "
            f"and contains(@id,'AttachmentFileUpload')]"
        )
        if file_input is None:
            file_input = page.wait_for_selector(
                f"xpath=//input[@type='file' and contains(@id,'{ctrl_prefix}') and contains(@id,'AttachmentFileUpload')]",
                timeout=15000,
            )
        file_input.set_input_files(renamed_path)
        time.sleep(0.5)

        desc_input = page.query_selector(
            f"xpath=//input[@type='text' and contains(@id,'{ctrl_prefix}') "
            f"and (contains(@id,'txtEmptyInsertDescription') or contains(@id,'txtFooterDescription'))]"
        )
        if desc_input:
            desc_input.fill("")
            desc_input.type(description)
        time.sleep(0.3)

        add_btn = page.wait_for_selector(
            f"xpath=//a[contains(@id,'{ctrl_prefix}') "
            f"and (contains(@id,'lbAddEmptyInsert') or contains(@id,'lbAddFooterInsert') or contains(@id,'lbAddQualificationProof'))]",
            timeout=15000,
        )
        add_btn.evaluate("el => el.scrollIntoView(true)")
        time.sleep(0.3)
        add_btn.evaluate("el => el.click()")
        wait_for_preloader(page)
        wait_for_aspx_load(page)
        time.sleep(1)

        log(f"PDF {i+1} attached as '{new_filename}'.")

    log(f"Done — qualification '{qualification_name}' with {len(pdf_paths)} PDF(s) saved for CIN {cin_str}.")
