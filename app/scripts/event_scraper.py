from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import time
from datetime import datetime
from bs4 import BeautifulSoup

from database.database import SessionLocal
from database.models import Location, Event317

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click


def clean_html(raw_html):
    if not raw_html:
        return None
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def _setup_events_table(page: Page):
    page.goto("https://sms.bader.mod.uk/events/default.aspx")
    wait_for_aspx_load(page)
    time.sleep(2)

    for checkbox in ["cbAdultIC", "cbMyUnit", "cbAttending"]:
        page.evaluate(
            f"document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody${checkbox}')[0].click();"
        )
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    page.evaluate(
        "document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$btnFilter')[0].click();"
    )
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    page.evaluate(
        "document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$cbToggleDisplay')[0].click();"
    )
    wait_for_preloader(page)
    wait_for_aspx_load(page)

    page.locator("[name='eventTable_length']").wait_for(timeout=20000)
    page.locator("[name='eventTable_length']").select_option(value="-1")
    wait_for_preloader(page)
    wait_for_aspx_load(page)


def _get_table_rows(page: Page):
    tbodies = page.query_selector_all("tbody")
    if not tbodies:
        raise Exception("Events table not found on page")
    rows = tbodies[0].query_selector_all("tr")
    if not rows:
        raise Exception("No events found in the table")
    return rows


def get_all_event_names(page: Page):
    _setup_events_table(page)
    rows = _get_table_rows(page)
    event_names = []
    for row in rows:
        columns = row.query_selector_all("td")
        if len(columns) < 2:
            raise Exception("Unexpected table format, not enough columns")
        event_names.append(columns[1].inner_text().replace("\n", " "))

    info_el = page.wait_for_selector("#eventTable_info", timeout=20000)
    try:
        number_of_events = int(info_el.inner_text().split(" ")[5])
    except (IndexError, ValueError):
        raise Exception(f"Failed to parse number of events from text: '{info_el.inner_text()}'")

    return event_names, number_of_events


def get_317_event_links(page: Page):
    _setup_events_table(page)
    rows = _get_table_rows(page)
    event_links_317 = []
    for row in rows:
        columns = row.query_selector_all("td")
        if len(columns) < 7:
            raise Exception("Unexpected table format, not enough columns")
        if columns[6].inner_text() != "317 (Failsworth & Newton Heath)":
            continue
        links = columns[1].query_selector_all("a")
        if len(links) > 1:
            event_links_317.append(links[1].get_attribute("href"))
    return event_links_317


def get_sub_app_attendees(page: Page, event_id, scraper_messages, scraper_lock):
    if not event_id:
        return []
    try:
        page.goto(f"https://sms.bader.mod.uk/events/details/subapps.aspx?eventId={event_id}")
        wait_for_aspx_load(page)
        wait_for_preloader(page)

        soup = BeautifulSoup(page.content(), "html.parser")
        btn_tags = soup.find_all(id=lambda x: x and "fvEventCard_lbAttendees" in x)
        num_sub_apps = len(btn_tags)

        if num_sub_apps == 0:
            return []

        with scraper_lock:
            scraper_messages.append(f"Found {num_sub_apps} sub-app{'s' if num_sub_apps != 1 else ''}, scraping each one.")

        sub_app_names = []
        for btn_tag in btn_tags:
            card = btn_tag.find_parent("div", class_="card")
            title_tag = card.find("h3", class_="card-title") if card else None
            if title_tag:
                link = title_tag.find("a")
                text = link.get_text(strip=True) if link else title_tag.get_text(strip=True)
            else:
                text = f"Sub-App {len(sub_app_names) + 1}"
            sub_app_names.append(text)

        results = []
        for i in range(num_sub_apps):
            sub_app_name = sub_app_names[i]
            with scraper_lock:
                scraper_messages.append(f"Scraping sub-app {i+1} of {num_sub_apps}: {sub_app_name}")
            button_id = f"ctl00_ctl00_cphBaseBody_cphBody_rpEvents_ctl{i:02d}_eventCard_fvEventCard_lbAttendees"

            try:
                button = page.wait_for_selector(f"#{button_id}", timeout=20000)
                classes = button.get_attribute("class") or ""

                if "disabled" in classes:
                    results.append({"sub_app_name": sub_app_name, "attendees": "No access/Disabled"})
                    continue

                safe_click(page, button)
                wait_for_aspx_load(page)

                modal = page.wait_for_selector(".modal.show .modal-content", state="visible", timeout=20000)
                wait_for_preloader(page)

                close_button = page.wait_for_selector(
                    "#ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_btnCloseModal",
                    timeout=20000,
                )

                modal_text = modal.inner_text()
                if "None of your Cadets are attending this event" in modal_text:
                    results.append({"sub_app_name": sub_app_name, "attendees": "No cadets attending this event."})
                    safe_click(page, close_button)
                    continue

                try:
                    page.locator("[name='ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_gvCadetsAttendees_length']").select_option(value="-1")
                    wait_for_preloader(page)
                except Exception:
                    pass

                tables = modal.query_selector_all("tbody")
                if not tables:
                    raise Exception("No rows found in attendees table")

                rows = tables[0].query_selector_all("tr")
                first_row_text = rows[0].inner_text().strip() if rows else ""

                if "None of your Cadets are attending this event" in first_row_text:
                    results.append({"sub_app_name": sub_app_name, "attendees": "No cadets attending this event."})
                else:
                    results.append({
                        "sub_app_name": sub_app_name,
                        "attendees": [[col.inner_text() for col in row.query_selector_all("td")] for row in rows],
                    })
                safe_click(page, close_button)
                page.wait_for_selector(".modal.show", state="hidden", timeout=10000)

            except Exception as e:
                with scraper_lock:
                    scraper_messages.append(f"Exception for sub-app '{sub_app_name}': {type(e).__name__}: {e}")
                results.append({"sub_app_name": sub_app_name, "attendees": "No cadets attending this event."})

        return results

    except Exception as e:
        with scraper_lock:
            scraper_messages.append(f"Sub-app scraping failed for event {event_id}: {e}")
        return []


def get_event_attendees(page: Page, event_names, number_of_events, scraper_messages, scraper_lock, stop_event=None):
    _setup_events_table(page)

    event_attendees = []
    for i in range(number_of_events):
        if stop_event and stop_event.is_set():
            return event_attendees
        with scraper_lock:
            scraper_messages.append(f"On event number {i+1} out of {number_of_events}")
        try:
            wait_for_preloader(page)
            wait_for_aspx_load(page)

            page.locator("[name='eventTable_length']").select_option(value="-1")
            wait_for_preloader(page)
            wait_for_aspx_load(page)

            event_id = None
            try:
                rows = _get_table_rows(page)
                cols = rows[i].query_selector_all("td")
                links = cols[1].query_selector_all("a") if len(cols) > 1 else []
                href = links[1].get_attribute("href") if len(links) > 1 else (links[0].get_attribute("href") if links else "")
                if href and "eventId=" in href:
                    event_id = href.split("eventId=")[1].split("&")[0]
            except Exception:
                pass

            event_attendees.append({"event_name": event_names[i], "event_id": event_id, "sub_apps": []})

            button_id = f"ctl00_ctl00_cphBaseBody_cphBody_lvEventDetails_ctrl{i}_lbAttendees"
            button = page.query_selector(f"#{button_id}")

            if button and "disabled" in (button.get_attribute("class") or ""):
                with scraper_lock:
                    scraper_messages.append(f"Skipping event {i+1} because the View button is disabled, checking sub-apps.")
                event_attendees[-1]["attendees"] = "No access/Disabled"
                sub_apps = get_sub_app_attendees(page, event_id, scraper_messages, scraper_lock)
                event_attendees[-1]["sub_apps"] = sub_apps
                if not sub_apps:
                    with scraper_lock:
                        scraper_messages.append(f"No sub-apps found for event {i+1}.")
                _setup_events_table(page)
                continue

            safe_click(page, button)
            wait_for_aspx_load(page)

            modal = page.wait_for_selector(".modal-content", state="visible", timeout=20000)
            wait_for_preloader(page)

            close_button = page.wait_for_selector(
                "#ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_btnCloseModal",
                timeout=20000,
            )

            modal_text = modal.inner_text()
            if "None of your Cadets are attending this event" in modal_text:
                event_attendees[-1]["attendees"] = "No cadets attending this event."
                safe_click(page, close_button)
                event_attendees[-1]["sub_apps"] = get_sub_app_attendees(page, event_id, scraper_messages, scraper_lock)
                _setup_events_table(page)
                continue

            try:
                page.locator("[name='ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_gvCadetsAttendees_length']").select_option(value="-1")
                wait_for_preloader(page)
            except Exception:
                pass

            # Use whole-page tbody search to match original Selenium behaviour
            tbodies = page.query_selector_all("tbody")
            if not tbodies:
                raise Exception("No rows found in attendees table")

            rows = tbodies[1].query_selector_all("tr") if len(tbodies) > 1 else tbodies[0].query_selector_all("tr")
            first_row_text = rows[0].inner_text().strip() if rows else ""

            if "None of your Cadets are attending this event" in first_row_text:
                event_attendees[-1]["attendees"] = "No cadets attending this event."
                safe_click(page, close_button)
                event_attendees[-1]["sub_apps"] = get_sub_app_attendees(page, event_id, scraper_messages, scraper_lock)
                _setup_events_table(page)
            else:
                event_attendees[-1]["attendees"] = [
                    [col.inner_text() for col in row.query_selector_all("td")]
                    for row in rows
                ]
                safe_click(page, close_button)

        except Exception:
            event_attendees[-1]["attendees"] = "No cadets attending this event."

    return event_attendees


def _get_input_text(page: Page, label_text: str) -> str:
    label = page.query_selector(f"xpath=//label[contains(., '{label_text}')]")
    if not label:
        raise Exception(f"Label '{label_text}' not found")
    input_box = page.query_selector(f"xpath=//label[contains(., '{label_text}')]/following::input[1]")
    if not input_box:
        raise Exception(f"Input for '{label_text}' not found")
    value = input_box.input_value()
    if value is None:
        raise Exception(f"Input field '{label_text}' has no value")
    return value.strip()


def _get_textarea(page: Page, label_text: str) -> str:
    textarea = page.query_selector(f"xpath=//label[contains(., '{label_text}')]/following::textarea[1]")
    if not textarea:
        raise Exception(f"Textarea for '{label_text}' not found")
    value = textarea.evaluate("el => el.value")
    if value is None:
        raise Exception(f"Textarea '{label_text}' has no value")
    return value.strip()


def get_317_event_info(page: Page, event_links_317, scraper_messages, scraper_lock, stop_event=None):
    session = SessionLocal()
    try:
        session.query(Event317).delete()
        session.commit()

        num_links = len(event_links_317)
        for index, link in enumerate(event_links_317):
            if stop_event and stop_event.is_set():
                break

            with scraper_lock:
                scraper_messages.append(f"On event {index+1} out of {num_links}")

            page.goto(link)
            wait_for_preloader(page)
            wait_for_aspx_load(page)
            page.wait_for_selector("body", timeout=10000)

            title = _get_input_text(page, "Title")
            reference = _get_input_text(page, "Reference")
            adult_ic = _get_input_text(page, "Adult IC")
            date_from = _get_input_text(page, "Date From")
            date_to = _get_input_text(page, "Date To")
            contact_number = _get_input_text(page, "Contact No.")
            location_name = _get_input_text(page, "Location")
            postcode = _get_input_text(page, "Postcode")
            cost = _get_input_text(page, "Cost Per Cadet")
            dress = _get_input_text(page, "Dress")
            description = clean_html(_get_textarea(page, "Description"))

            def parse_date(d):
                if not d:
                    return None
                try:
                    return datetime.strptime(d, "%d/%m/%Y %H:%M")
                except ValueError:
                    return None

            date_from = parse_date(date_from)
            date_to = parse_date(date_to)

            try:
                cost_int = int(float(cost))
            except Exception:
                cost_int = 0

            location = session.query(Location).filter_by(first_line=location_name, postcode=postcode).first()
            if not location:
                location = Location(
                    first_line=location_name or "Unknown",
                    postcode=postcode or "Unknown",
                )
                session.add(location)
                session.commit()

            event = Event317(
                title=title or "Untitled",
                reference=reference or "Error",
                adult_ic=adult_ic or "N/A",
                contact_number=int(contact_number) if contact_number and contact_number.isdigit() else 0,
                date_from=date_from,
                date_to=date_to,
                location_id=location.id,
                cost=cost_int,
                dress=dress or "Unknown",
                description=description or "Unknown",
            )
            session.add(event)
            session.commit()

    except Exception as e:
        session.rollback()
        print(f"Error during event sync: {e}")
        raise
    finally:
        session.close()
