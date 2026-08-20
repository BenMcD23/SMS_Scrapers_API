from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
import json
import threading

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click
from scripts.attendance import get_attendance
from scripts.profiles import CADETS, collect_profile_links, open_profile
from scripts.tables import ensure_all_rows_shown, entries_total, read_rows, wait_for_full_draw

scraper_lock = threading.Lock()


def get_cadet_names(page: Page):
    """Names, count, and the postback link for each row, off one drawn table.

    The links are collected here because this is the only point in a scrape where
    every row is rendered anyway. Handing them to the profile loop is what lets
    it skip redrawing the table once per cadet.
    """
    page.goto(CADETS["url"])
    wait_for_aspx_load(page)

    ensure_all_rows_shown(page, "Cadets_length", "Cadets")
    wait_for_full_draw(page, "Cadets")

    rows = read_rows(page, "#Cadets")
    if not rows:
        raise Exception("No cadet rows found")

    cadetNames = [
        " ".join(row[i].replace("\n", " ") for i in [1, 2]).strip()
        for row in rows
        if len(row) > 2
    ]

    info_el = page.wait_for_selector("#Cadets_info", timeout=20000)
    info_text = info_el.inner_text()

    numberOfCadets = entries_total(info_text)
    if numberOfCadets is None:
        raise Exception(f"Failed to parse number of cadets from text: '{info_text}'")

    profile_links = collect_profile_links(page, CADETS["link_marker"])

    return cadetNames, numberOfCadets, profile_links


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

        # The edit link only exists once the Classification form view has
        # rendered — the condition the fixed sleeps above were standing in for.
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

        print(f"Classification report: {pages} page(s), {len(result)} cadets matched")

    except Exception as e:
        print(f"Warning: Could not load classification report: {e}")
    return result


def _read_qual_rows(page: Page):
    """Every direct-child row of the qualifications table, in one round trip.

    Returns the row's class, its cell texts, and whether its hidden sibling row
    carries a proof attachment — everything _parse_qual_rows needs, so a cadet
    with thirty quals costs one CDP call rather than a hundred.
    """
    return page.evaluate(
        """() => {
            const body = document.querySelector('tbody');
            if (!body) return [];
            return Array.from(body.children).filter(el => el.tagName === 'TR').map(tr => {
                const sib = tr.nextElementSibling;
                return {
                    className: tr.className || '',
                    cells: Array.from(tr.querySelectorAll('td')).map(td => td.innerText),
                    // The proofs table for each qual is already rendered in the
                    // hidden sibling row — a View link (hlAttachment) only exists
                    // when at least one proof is attached. No clicking needed.
                    hasAttachment: !!(sib && sib.classList.contains('sibling')
                        && sib.querySelector("a[id*='hlAttachment']")),
                };
            });
        }"""
    )


def _qual_date(text):
    try:
        return datetime.strptime((text or "").strip(), "%d/%m/%Y")
    except ValueError:
        return None


def _parse_qual_rows(rows, attachment_check_quals):
    """Qualification records out of the raw rows _read_qual_rows returned."""
    quals = []
    for row in rows:
        if "sibling" in (row.get("className") or ""):
            continue  # hidden proof/attachment row, not a qualification
        cells = row.get("cells") or []
        if not cells or not cells[0].strip():
            continue

        qual_type = cells[0].replace("\n", " ").strip()
        quals.append({
            "qual_type": qual_type,
            "status": "true",
            "date_achieved": _qual_date(cells[1]) if len(cells) > 1 else None,
            "date_expires": _qual_date(cells[2]) if len(cells) > 2 else None,
            # Left as None — not False — for quals nobody asked us to check.
            "has_attachment": bool(row.get("hasAttachment"))
            if qual_type.casefold() in attachment_check_quals else None,
        })
    return quals


def get_cadet_info_and_qualifications(page: Page, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=None, attachment_check_quals=None, profile_links=None):
    attachment_check_quals = attachment_check_quals or set()  # casefolded exact qual names to check for proof attachments
    profile_links = profile_links or {}
    cadet_data = []
    fast_opens = 0
    classifications_by_name = get_all_classifications(page)

    for i in range(numberOfCadets):
        # if i == 2:
        #     break
        if stop_event and stop_event.is_set():
            return cadet_data

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Scraping cadet {i + 1} of {numberOfCadets}: {cadetNames[i]}"}))

        if open_profile(page, i, profile_links, CADETS, numberOfCadets):
            fast_opens += 1

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

        cadetQualifications = []
        try:
            # The tab is only really open once its table is there, which is what
            # the fixed sleeps in the loop above were waiting for.
            page.wait_for_selector("tbody", timeout=10000)
            cadetQualifications = _parse_qual_rows(_read_qual_rows(page), attachment_check_quals)
        except Exception as e:
            print(f"Warning: Could not extract qualifications for {cadetNames[i]}: {e}")

        cadetAttendance = get_attendance(page)

        cadet_data.append({
            "cin": cin,
            "first_name": first_name,
            "last_name": last_name,
            "rank": rank,
            "flight": flight,
            "date_of_birth": date_of_birth,
            "classification": classification,
            "qualifications": cadetQualifications,
            "attendance": cadetAttendance,
        })

    with scraper_lock:
        scraper_messages.append(json.dumps({
            "type": "info",
            "value": f"Opened {fast_opens} of {len(cadet_data)} profile(s) without redrawing the cadet list.",
        }))

    return cadet_data
