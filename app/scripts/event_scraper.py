from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, StaleElementReferenceException, TimeoutException

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


def _setup_events_table(driver):
    """Navigate to the events page, apply the all-events filter, and switch to full table view."""
    driver.get("https://sms.bader.mod.uk/events/default.aspx")
    wait_for_aspx_load(driver)
    time.sleep(2)

    for checkbox in ['cbAdultIC', 'cbMyUnit', 'cbAttending']:
        driver.execute_script(
            f"document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody${checkbox}')[0].click();"
        )
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    driver.execute_script(
        "document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$btnFilter')[0].click();"
    )
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    driver.execute_script(
        "document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$cbToggleDisplay')[0].click();"
    )
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    Select(WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.NAME, 'eventTable_length'))
    )).select_by_value('-1')
    wait_for_preloader(driver)
    wait_for_aspx_load(driver)


def _get_table_rows(driver):
    table = driver.find_elements(by=By.XPATH, value='//*/tbody')
    if not table:
        raise Exception("Events table not found on page")
    rows = table[0].find_elements(by=By.TAG_NAME, value="tr")
    if not rows:
        raise Exception("No events found in the table")
    return rows


def get_all_event_names(driver):
    """
    Set up the events table and return all event names and the total count.
    Used by the cadet-event attendees scraper.
    """
    _setup_events_table(driver)

    rows = _get_table_rows(driver)
    event_names = []
    for row in rows:
        columns = row.find_elements(by=By.TAG_NAME, value='td')
        if len(columns) < 2:
            raise Exception("Unexpected table format, not enough columns")
        event_names.append(columns[1].text.replace("\n", " "))

    info_element = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.ID, 'eventTable_info'))
    )
    try:
        number_of_events = int(info_element.text.split(" ")[5])
    except (IndexError, ValueError):
        raise Exception(f"Failed to parse number of events from text: '{info_element.text}'")

    return event_names, number_of_events


def get_317_event_links(driver):
    """
    Set up the events table and return the detail-page URLs for 317-unit events only.
    Used by the 317 event info scraper.
    """
    _setup_events_table(driver)

    rows = _get_table_rows(driver)
    event_links_317 = []
    for row in rows:
        columns = row.find_elements(by=By.TAG_NAME, value='td')
        if len(columns) < 7:
            raise Exception("Unexpected table format, not enough columns")

        if columns[6].text != "317 (Failsworth & Newton Heath)":
            continue

        links = columns[1].find_elements(By.TAG_NAME, 'a')
        if len(links) > 1:
            event_links_317.append(links[1].get_attribute('href'))

    return event_links_317


def get_sub_app_attendees(driver, event_id, scraper_messages, scraper_lock):
    """Scrape attendees for each sub-app of an event. Returns [] if no sub-apps exist."""
    if not event_id:
        return []
    try:
        driver.get(f"https://sms.bader.mod.uk/events/details/subapps.aspx?eventId={event_id}")
        wait_for_aspx_load(driver)
        wait_for_preloader(driver)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        btn_tags = soup.find_all(id=lambda x: x and 'fvEventCard_lbAttendees' in x)
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
                button = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.ID, button_id))
                )
                classes = driver.find_element(By.ID, button_id).get_attribute("class") or ""

                if "disabled" in classes:
                    results.append({"sub_app_name": sub_app_name, "attendees": "No access/Disabled"})
                    continue

                safe_click(driver, button)
                wait_for_aspx_load(driver)

                modal = WebDriverWait(driver, 20).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, ".modal.show .modal-content"))
                )
                wait_for_preloader(driver)

                close_button = WebDriverWait(driver, 20).until(
                    EC.element_to_be_clickable((
                        By.ID,
                        "ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_btnCloseModal"
                    ))
                )

                if "None of your Cadets are attending this event" in modal.text:
                    results.append({"sub_app_name": sub_app_name, "attendees": "No cadets attending this event."})
                    safe_click(driver, close_button)
                    continue

                try:
                    Select(WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
                        (By.NAME, 'ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_gvCadetsAttendees_length')
                    ))).select_by_value('-1')
                    wait_for_preloader(driver)
                except Exception:
                    pass

                tables = modal.find_elements(By.XPATH, './/tbody')
                if not tables:
                    raise Exception("No rows found in attendees table")

                rows = tables[0].find_elements(By.TAG_NAME, "tr")

                if "None of your Cadets are attending this event" in rows[0].text.strip():
                    results.append({"sub_app_name": sub_app_name, "attendees": "No cadets attending this event."})
                else:
                    results.append({
                        "sub_app_name": sub_app_name,
                        "attendees": [[col.text for col in row.find_elements(By.TAG_NAME, 'td')] for row in rows],
                    })
                safe_click(driver, close_button)
                WebDriverWait(driver, 10).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, ".modal.show"))
                )

            except (ElementClickInterceptedException, StaleElementReferenceException, TimeoutException) as e:
                with scraper_lock:
                    scraper_messages.append(f"Exception for sub-app '{sub_app_name}': {type(e).__name__}: {e}")
                results.append({"sub_app_name": sub_app_name, "attendees": "No cadets attending this event."})

        return results

    except Exception as e:
        with scraper_lock:
            scraper_messages.append(f"Sub-app scraping failed for event {event_id}: {e}")
        return []


def get_event_attendees(driver, event_names, number_of_events, scraper_messages, scraper_lock, stop_event=None):
    """Scrape the list of attending cadets for every event."""
    _setup_events_table(driver)

    event_attendees = []
    for i in range(number_of_events):
        if stop_event and stop_event.is_set():
            return event_attendees
        with scraper_lock:
            scraper_messages.append(f"On event number {i+1} out of {number_of_events}")
        try:
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)

            # Re-select "show all" each iteration — modals can reset the table length
            Select(WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.NAME, 'eventTable_length'))
            )).select_by_value('-1')
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)

            # Extract eventId from the row's detail link for sub-app fallback
            event_id = None
            try:
                rows = _get_table_rows(driver)
                cols = rows[i].find_elements(By.TAG_NAME, 'td')
                links = cols[1].find_elements(By.TAG_NAME, 'a') if len(cols) > 1 else []
                href = links[1].get_attribute('href') if len(links) > 1 else (links[0].get_attribute('href') if links else '')
                if href and 'eventId=' in href:
                    event_id = href.split('eventId=')[1].split('&')[0]
            except Exception:
                pass

            event_attendees.append({"event_name": event_names[i], "event_id": event_id, "sub_apps": []})

            button_id = f"ctl00_ctl00_cphBaseBody_cphBody_lvEventDetails_ctrl{i}_lbAttendees"
            button = driver.find_element(By.ID, button_id)

            if "disabled" in button.get_attribute("class"):
                with scraper_lock:
                    scraper_messages.append(f"Skipping event {i+1} because the View button is disabled, checking sub-apps.")
                event_attendees[-1]["attendees"] = "No access/Disabled"
                sub_apps = get_sub_app_attendees(driver, event_id, scraper_messages, scraper_lock)
                event_attendees[-1]["sub_apps"] = sub_apps
                if not sub_apps:
                    with scraper_lock:
                        scraper_messages.append(f"No sub-apps found for event {i+1}.")
                _setup_events_table(driver)
                continue

            safe_click(driver, button)
            wait_for_aspx_load(driver)

            modal = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "modal-content"))
            )
            wait_for_preloader(driver)

            close_button = (
                WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.CLASS_NAME, "modal-footer")))
                .find_element(By.ID, 'ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_btnCloseModal')
            )

            if "None of your Cadets are attending this event" in modal.text:
                event_attendees[-1]["attendees"] = "No cadets attending this event."
                safe_click(driver, close_button)
                event_attendees[-1]["sub_apps"] = get_sub_app_attendees(driver, event_id, scraper_messages, scraper_lock)
                _setup_events_table(driver)
                continue

            try:
                Select(WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
                    (By.NAME, 'ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_gvCadetsAttendees_length')
                ))).select_by_value('-1')
                wait_for_preloader(driver)
            except Exception:
                pass

            div = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "modal-content"))
            )
            tables = div.find_elements(By.XPATH, '//*/tbody')
            if not tables:
                raise Exception("No rows found in attendees table")

            rows = tables[1].find_elements(By.TAG_NAME, "tr")
            if "None of your Cadets are attending this event" in rows[0].text.strip():
                event_attendees[-1]["attendees"] = "No cadets attending this event."
                safe_click(driver, close_button)
                event_attendees[-1]["sub_apps"] = get_sub_app_attendees(driver, event_id, scraper_messages, scraper_lock)
                _setup_events_table(driver)
            else:
                event_attendees[-1]["attendees"] = [
                    [col.text for col in row.find_elements(By.TAG_NAME, 'td')]
                    for row in rows
                ]
                safe_click(driver, close_button)

        except (ElementClickInterceptedException, StaleElementReferenceException, TimeoutException):
            event_attendees[-1]["attendees"] = "No cadets attending this event."

    return event_attendees


def _get_input_text(driver, label_text):
    label = driver.find_element(By.XPATH, f"//label[contains(., '{label_text}')]")
    input_box = label.find_element(By.XPATH, "following::input[1]")
    value = input_box.get_attribute("value")
    if value is None:
        raise Exception(f"Input field '{label_text}' has no value")
    return value.strip()


def _get_textarea(driver, label_text):
    label = driver.find_element(By.XPATH, f"//label[contains(., '{label_text}')]")
    textarea = label.find_element(By.XPATH, "following::textarea[1]")
    value = textarea.get_attribute("value")
    if value is None:
        raise Exception(f"Textarea '{label_text}' has no value")
    return value.strip()


def get_317_event_info(driver, event_links_317, scraper_messages, scraper_lock, stop_event=None):
    """Scrape full details for each 317-unit event and sync them to the database."""
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

            driver.get(link)
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

            title = _get_input_text(driver, "Title")
            reference = _get_input_text(driver, "Reference")
            adult_ic = _get_input_text(driver, "Adult IC")
            date_from = _get_input_text(driver, "Date From")
            date_to = _get_input_text(driver, "Date To")
            contact_number = _get_input_text(driver, "Contact No.")
            location_name = _get_input_text(driver, "Location")
            postcode = _get_input_text(driver, "Postcode")
            cost = _get_input_text(driver, "Cost Per Cadet")
            dress = _get_input_text(driver, "Dress")
            description = clean_html(_get_textarea(driver, "Description"))

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
