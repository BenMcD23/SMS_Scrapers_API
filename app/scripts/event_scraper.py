from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, StaleElementReferenceException, TimeoutException

import time
from datetime import datetime
import urllib.parse
from bs4 import BeautifulSoup

from database.database import Base, engine, SessionLocal
from database.models import Cadet, Location, Event317, AllEvent, CadetEvent, BanNotification

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click

def clean_html(raw_html):
    if not raw_html:
        return None
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator=" ", strip=True)

def get_event_names_and_317_links(driver):
    # Go to events page
    driver.get("https://sms.bader.mod.uk/events/default.aspx")
    wait_for_aspx_load(driver)
    time.sleep(2)
    # Filter to all events 
    for checkbox in ['cbAdultIC', 'cbMyUnit', 'cbAttending']:
        driver.execute_script(f"document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody${checkbox}')[0].click();")

    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    # Apply/click to apply filter
    driver.execute_script("document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$btnFilter')[0].click();")

    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    # Turn into table format
    driver.execute_script("document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$cbToggleDisplay')[0].click();")

    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    # Make it so all events are shown on page
    Select(WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.NAME, 'eventTable_length')))).select_by_value('-1')

    wait_for_preloader(driver)
    wait_for_aspx_load(driver)

    # See how many rows/events there are
    rows = driver.find_elements(By.XPATH, '//*/tbody')[0].find_elements(By.TAG_NAME, "tr")

    # Loop through events and get names ect..
    event_names = []
    event_links_317 = []  # store URLs

    table = driver.find_elements(by=By.XPATH, value='//*/tbody')
    if not table:
        raise Exception("Events table not found on page")

    rows = table[0].find_elements(by=By.TAG_NAME, value="tr")
    if not rows:
        raise Exception("No events found in the table")
    
    for row in rows:
        columns = row.find_elements(by=By.TAG_NAME, value='td')

        if len(columns) < 7:
            raise Exception("Unexpected table format, not enough columns")
        title_link = None

        for index, col in enumerate(columns):

            if index == 1: # the column with the event title
                event_names.append((col.text).replace("\n", " "))

                links = col.find_elements(By.TAG_NAME, 'a')
                if len(links) > 1:
                    title_link = links[1].get_attribute('href')  # get URL of the event, its the 2nd url as there is a little dropdown menu

            # only add the url to the list if its a 317 event
            if index == 6 and col.text == "317 (Failsworth & Newton Heath)":
                if title_link:
                    event_links_317.append(title_link)  # save the URL

    info_element = WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.ID, 'eventTable_info'))
    )
    info_text = info_element.text
    try:
        number_of_events = int(info_text.split(" ")[5])
    except (IndexError, ValueError):
        raise Exception(f"Failed to parse number of events from text: '{info_text}'")

    return event_names, number_of_events, event_links_317

def get_event_attendees(driver, event_names, number_of_events, scraper_messages, scraper_lock):
    driver.get("https://sms.bader.mod.uk/events/default.aspx")

    wait_for_aspx_load(driver)

    try:
        # Filter to all events 
        for checkbox in ['cbAdultIC', 'cbMyUnit', 'cbAttending']:
            driver.execute_script(f"document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody${checkbox}')[0].click();")
        wait_for_preloader(driver)
        wait_for_aspx_load(driver)

        # Apply/click to apply filter
        driver.execute_script("document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$btnFilter')[0].click();")
        wait_for_preloader(driver)
        wait_for_aspx_load(driver)

        # 1. Turn into table format
        driver.execute_script("document.getElementsByName('ctl00$ctl00$cphBaseBody$cphBody$cbToggleDisplay')[0].click();")
        wait_for_preloader(driver)
        wait_for_aspx_load(driver)

        # 2. Show all rows
        Select(WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.NAME, 'eventTable_length')))).select_by_value('-1')
        wait_for_preloader(driver)
        wait_for_aspx_load(driver)
    except Exception as e:
        print(f"Setup error (maybe already in table mode): {e}")
    
    event_attendees = []
    for i in range(number_of_events):
        with scraper_lock:
            scraper_messages.append(f"On event number {i+1} out of {number_of_events}")
        try:
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)

            # 2. Show all rows
            Select(WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.NAME, 'eventTable_length')))).select_by_value('-1')
            wait_for_preloader(driver)
            wait_for_aspx_load(driver)

            event_attendees.append({"event_name": event_names[i]})
            
            button_id = f"ctl00_ctl00_cphBaseBody_cphBody_lvEventDetails_ctrl{i}_lbAttendees"
            button = driver.find_element(By.ID, button_id)

            # 2. Check if 'disabled' is in the class string
            if "disabled" in button.get_attribute("class"):
                with scraper_lock:
                    scraper_messages.append(f"Skipping event {i+1} because the View button is disabled.")

                event_attendees.append({"event_name": event_names[i], "attendees": "No access/Disabled"})
                continue  # Move to the next event in the loop
            
            safe_click(driver, button)
            wait_for_aspx_load(driver)


            modal = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.CLASS_NAME, "modal-content")))
            wait_for_preloader(driver)

            close_button = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.CLASS_NAME, "modal-footer")))\
                .find_element(By.ID, 'ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_btnCloseModal')
            
            if "None of your Cadets are attending this event" in modal.text:
                event_attendees[-1]["attendees"] = "No cadets attending this event."
                safe_click(driver, close_button)
                continue
            else:
                try:
                    Select(WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
                        (By.NAME, 'ctl00_ctl00_cphBaseBody_cphBody_eventNoticeboard_gvCadetsAttendees_length')))).select_by_value('-1')
                    wait_for_preloader(driver)
                except:
                    pass


            div = WebDriverWait(driver, 20).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "modal-content"))
            )

            tables = div.find_elements(By.XPATH, '//*/tbody')

            if not tables:
                raise Exception("No rows found in attendees table")
        
            rows = tables[1].find_elements(By.TAG_NAME, "tr")

            # Check if the first row contains the "No cadets" message
            first_row_text = rows[0].text.strip()
            if "None of your Cadets are attending this event" in first_row_text:
                attendees = "No cadets attending this event."
            else:
                # Otherwise, proceed with the normal list comprehension
                attendees = [[col.text for col in row.find_elements(By.TAG_NAME, 'td')] for row in rows]

            event_attendees[-1]["attendees"] = attendees

            safe_click(driver, close_button)

        except (ElementClickInterceptedException, StaleElementReferenceException, TimeoutException) :
            event_attendees[-1]["attendees"] = "No cadets attending this event."

    return event_attendees





def get_input_text(driver, label_text):
    # find the label with the specific text
    label = driver.find_element(By.XPATH, f"//label[contains(., '{label_text}')]")
    # get the following input box no matter what, needs the [1] as they have decided to put cost in a div
    input_box = label.find_element(By.XPATH, "following::input[1]")

    value = input_box.get_attribute("value")
    if value is None:
        raise Exception(f"Input field '{label_text}' has no value")
    return value.strip()


def get_textarea(driver, label_text):
    label = driver.find_element(By.XPATH, f"//label[contains(., '{label_text}')]")
    textarea = label.find_element(By.XPATH, "following::textarea[1]")
    value = textarea.get_attribute("value")
    if value is None:
        raise Exception(f"Textarea '{label_text}' has no value")
    return value.strip()

def get_317_event_info(driver, event_links_317, scraper_messages, scraper_lock):
    driver.get("https://sms.bader.mod.uk/events/default.aspx")
    session = SessionLocal()
    try:
        # Delete all current entires, as dont want duplicate
        session.query(Event317).delete()
        session.commit()

        num_links = len(event_links_317)
        for index, link in enumerate(event_links_317):
            with scraper_lock:
                scraper_messages.append(f"On event {index+1} out of {num_links}")

            driver.get(link)

            wait_for_preloader(driver)
            wait_for_aspx_load(driver)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # Extract fields
            title = get_input_text(driver, "Title")
            reference = get_input_text(driver, "Reference")
            adult_ic = get_input_text(driver, "Adult IC")
            date_from = get_input_text(driver, "Date From")
            date_to = get_input_text(driver, "Date To")
            contact_number = get_input_text(driver, "Contact No.")
            location_name = get_input_text(driver, "Location")
            postcode = get_input_text(driver, "Postcode")
            cost = get_input_text(driver, "Cost Per Cadet")
            dress = get_input_text(driver, "Dress")
            description = clean_html(get_textarea(driver, "Description"))

            # Convert date strings to datetime
            def parse_date(d):
                if not d:
                    return None
                try:
                    return datetime.strptime(d, "%d/%m/%Y %H:%M")
                except ValueError:
                    return None

            date_from = parse_date(date_from)
            date_to = parse_date(date_to)

            # Convert cost to integer (remove decimals if needed)
            try:
                cost_int = int(float(cost))
            except:
                cost_int = 0

            # Add Location (or reuse if already exists)
            location = (
                session.query(Location)
                .filter_by(first_line=location_name, postcode=postcode)
                .first()
            )
            if not location:
                location = Location(
                    first_line=location_name or "Unknown",
                    postcode=postcode or "Unknown",
                )
                session.add(location)
                session.commit()

            # Insert Event
            event = Event317(
                title=title or "Untitled",
                reference=reference or "Error",
                adult_ic=adult_ic or "N/A",
                contact_number=int(contact_number) if contact_number and contact_number.isdigit() else 0,
                date_from=date_from or None,
                date_to=date_to or None,
                location_id=location.id,
                cost=cost_int or 0,
                dress=dress or "Unknown",
                description=description or "Unknown",
            )

            session.add(event)
            session.commit()

        session.close()
    except Exception as e:
        session.rollback()
        print(f"Error during event sync: {e}")
        raise
    finally:
        session.close()