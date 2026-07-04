from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
import json
import time
import threading

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click

scraper_lock = threading.Lock()


def get_cadet_names(page: Page):
    page.goto("https://sms.bader.mod.uk/cadets/default.aspx")
    wait_for_aspx_load(page)

    page.locator("[name='Cadets_length']").select_option(value="-1")

    cadetNames = []
    tbodies = page.query_selector_all("tbody")
    if not tbodies:
        raise Exception("Cadet table not found")
    rows = tbodies[0].query_selector_all("tr")
    if not rows:
        raise Exception("No cadet rows found")

    for row in rows:
        columns = row.query_selector_all("td")
        name = " ".join(columns[i].inner_text().replace("\n", " ") for i in [1, 2])
        cadetNames.append(name.strip())

    info_el = page.wait_for_selector("#Cadets_info", timeout=20000)
    info_text = info_el.inner_text()

    try:
        numberOfCadets = int(info_text.split(" ")[5])
    except (IndexError, ValueError):
        raise Exception(f"Failed to parse number of cadets from text: '{info_text}'")

    return cadetNames, numberOfCadets


CLASSIFICATION_LEVELS = [
    ("Master Air Cadet",  "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_StaffCadetPart1ResultTypeLabel"),
    ("Senior Cadet",      "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_SeniorCadetResultTypeLabel"),
    ("Leading Cadet",     "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_LeadingCadetResultTypeLabel"),
    ("First Class Cadet", "ctl00_ctl00_cphBaseBody_cphBody_fvClassification_FirstClassPart3TypeLabel"),
]


def get_classification(page: Page):
    try:
        class_tab_ids = [
            "ctl00_ctl00_cphBaseBody_cphBody_TabsCadet1_Classification",
            "ctl00_ctl00_cphBaseBody_cphBody_TabsCadet1_Summary",
        ]
        for elem_id in class_tab_ids:
            tab_element = page.wait_for_selector(f"#{elem_id}", timeout=15000)
            safe_click(page, tab_element)
            wait_for_preloader(page)
            wait_for_aspx_load(page)
            time.sleep(0.5)

        page.wait_for_selector(
            "#ctl00_ctl00_cphBaseBody_cphBody_fvClassification_lbEdit",
            timeout=15000,
        )

        for label, input_id in CLASSIFICATION_LEVELS:
            try:
                el = page.query_selector(f"#{input_id}")
                if el is None:
                    continue
                value = el.get_attribute("value") or ""
                if value.strip().lower() == "pass":
                    return label
            except Exception:
                continue
        return "Junior Cadet"

    except Exception as e:
        print(f"Warning: Could not extract classification: {e}")
        return None


def _norm_name(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _report_signature(page: Page):
    try:
        el = page.query_selector("div[id^='VisibleReportContent']")
        return el.inner_text()[:300] if el else ""
    except Exception:
        return ""


def _parse_classification_page(page: Page, result: dict, current):
    content = page.query_selector("div[id^='VisibleReportContent']")
    if not content:
        return current
    for row in content.query_selector_all("tr[valign='top']"):
        tds = row.query_selector_all("td")
        if len(tds) < 4:
            continue
        classification_cell = tds[-3].inner_text().strip()
        rank_cell = tds[-2].inner_text().strip()
        name_cell = tds[-1].inner_text().strip()
        if classification_cell == "Classification" and name_cell == "Name":
            continue
        if classification_cell and not rank_cell and not name_cell:
            current = classification_cell
        elif name_cell and rank_cell and not classification_cell:
            result[_norm_name(name_cell)] = current
    return current


def get_all_classifications(page: Page):
    result = {}
    try:
        page.goto("https://sms.bader.mod.uk/reports/unitPersonnelClassifications.aspx")
        wait_for_preloader(page)
        wait_for_aspx_load(page)

        page.wait_for_function(
            "() => document.querySelectorAll(\"div[id^='VisibleReportContent'] tr[valign='top']\").length > 3",
            timeout=60000,
        )

        next_id = "ctl00_ctl00_cphBaseBody_cphBody_rptvwReport_ctl05_ctl00_Next_ctl00_ctl00"
        current = None
        pages = 0
        while True:
            current = _parse_classification_page(page, result, current)
            pages += 1

            next_btn = page.query_selector(f"#{next_id}")
            if not next_btn:
                break
            if not next_btn.is_visible() or pages > 100:
                break

            before = _report_signature(page)
            safe_click(page, next_btn)
            try:
                page.wait_for_function(
                    "(before) => { const el = document.querySelector(\"div[id^='VisibleReportContent']\"); "
                    "return !!el && el.innerText.slice(0,300) !== before && el.innerText.length > 0; }",
                    arg=before,
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                print(f"Warning: classification report stopped advancing at page {pages}")
                break
            wait_for_aspx_load(page)
            time.sleep(0.3)

        print(f"Classification report: {pages} page(s), {len(result)} cadets matched")

    except Exception as e:
        print(f"Warning: Could not load classification report: {e}")
    return result


def get_cadet_info_and_qualifications(page: Page, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=None, attachment_check_quals=None):
    attachment_check_quals = attachment_check_quals or set()  # casefolded exact qual names to check for proof attachments
    cadet_data = []
    classifications_by_name = get_all_classifications(page)

    for i in range(numberOfCadets):
        if stop_event and stop_event.is_set():
            return cadet_data

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Scraping cadet {i + 1} of {numberOfCadets}: {cadetNames[i]}"}))

        page.goto("https://sms.bader.mod.uk/cadets/default.aspx")
        wait_for_aspx_load(page)

        page.locator("[name='Cadets_length']").select_option(value="-1")
        wait_for_preloader(page)
        wait_for_aspx_load(page)

        link = page.wait_for_selector(
            f"#ctl00_ctl00_cphBaseBody_cphBody_lvCadets_ctrl{i}_lbFamilyName",
            timeout=20000,
        )
        safe_click(page, link)
        wait_for_preloader(page)
        wait_for_aspx_load(page)

        # CIN
        try:
            cin_label = page.query_selector("#ctl00_ctl00_cphBaseBody_cphBody_overview_fvProfile_lblPersonnelNumber")
            cin = cin_label.evaluate(
                "el => { let sib = el.nextElementSibling; while(sib) { if(sib.tagName==='H6') return sib.innerText.trim(); sib=sib.nextElementSibling; } return ''; }"
            )
        except Exception:
            cin = None

        # Rank
        try:
            rank_el = page.query_selector(".card-subtitle")
            rank = rank_el.inner_text().strip() if rank_el else None
        except Exception:
            rank = None

        # First name
        try:
            fn_el = page.query_selector("#ctl00_ctl00_cphBaseBody_cphBody_fvCadetDetail_txtGivenName")
            first_name = fn_el.input_value().strip() if fn_el else None
        except Exception:
            first_name = cadetNames[i].split()[0] if cadetNames[i] else None

        # Last name
        try:
            ln_el = page.query_selector("#ctl00_ctl00_cphBaseBody_cphBody_fvCadetDetail_txtSurname")
            last_name = ln_el.input_value().strip() if ln_el else None
        except Exception:
            last_name = cadetNames[i].split()[-1] if cadetNames[i] else None

        # Date of birth
        try:
            dob_el = page.query_selector(
                "xpath=//label[normalize-space()='Date of Birth']/following-sibling::input[@type='text'][1]"
            )
            if not dob_el:
                dob_el = page.query_selector(
                    "xpath=//label[normalize-space()='Date of Birth']/../following-sibling::div//input[@type='text'][1]"
                )
            dob_str = dob_el.input_value().strip() if dob_el else ""
            date_of_birth = datetime.strptime(dob_str, "%d/%m/%Y") if dob_str else None
        except Exception:
            date_of_birth = None

        # Flight
        try:
            flight_el = page.query_selector("#ctl00_ctl00_cphBaseBody_cphBody_fvCadetDetail_ddlFlightEdit")
            flight = None
            if flight_el:
                selected = flight_el.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''")
                flight = selected.strip() if selected and selected.strip() != "Please Select ..." else None
        except Exception:
            flight = None

        # Classification
        classification = classifications_by_name.get(_norm_name(f"{first_name} {last_name}"))
        if classification is None:
            classification = get_classification(page)

        # Navigate to qualifications
        for tab_text in ["Qualifications & Awards", "General Qualifications"]:
            tab_el = page.wait_for_selector(f"xpath=//a[contains(text(), '{tab_text}')]", timeout=15000)
            safe_click(page, tab_el)
            wait_for_preloader(page)
            wait_for_aspx_load(page)
            time.sleep(0.5)

        wait_for_aspx_load(page)

        cadetQualifications = []
        try:
            wait_for_aspx_load(page)
            tbody = page.wait_for_selector("tbody", timeout=10000)
            rows = tbody.query_selector_all("tr")

            for row in rows:
                cols = row.query_selector_all("td")
                if not cols or not cols[0].inner_text().strip():
                    continue

                qual_type = cols[0].inner_text().replace("\n", " ").strip()

                date_achieved = None
                if len(cols) > 1:
                    try:
                        date_achieved = datetime.strptime(cols[1].inner_text().strip(), "%d/%m/%Y")
                    except (ValueError, IndexError):
                        pass

                date_expires = None
                if len(cols) > 2:
                    try:
                        date_expires = datetime.strptime(cols[2].inner_text().strip(), "%d/%m/%Y")
                    except (ValueError, IndexError):
                        pass

                has_attachment = None
                if qual_type.casefold() in attachment_check_quals:
                    # The proofs table for each qual is already rendered in the
                    # hidden sibling row — a View link (hlAttachment) only exists
                    # when at least one proof is attached. No clicking needed.
                    has_attachment = bool(row.evaluate(
                        "el => { const sib = el.nextElementSibling;"
                        " return !!(sib && sib.classList.contains('sibling')"
                        " && sib.querySelector(\"a[id*='hlAttachment']\")); }"
                    ))

                cadetQualifications.append({
                    "qual_type": qual_type,
                    "status": "true",
                    "date_achieved": date_achieved,
                    "date_expires": date_expires,
                    "has_attachment": has_attachment,
                })

        except Exception as e:
            print(f"Warning: Could not extract qualifications for {cadetNames[i]}: {e}")

        cadet_data.append({
            "cin": cin,
            "first_name": first_name,
            "last_name": last_name,
            "rank": rank,
            "flight": flight,
            "date_of_birth": date_of_birth,
            "classification": classification,
            "qualifications": cadetQualifications,
        })

    return cadet_data
