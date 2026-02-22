from scripts.scraper_utils import init_scraper, push_to_google_apps_script, login
from scripts.quali_scraper import *
from scripts.event_scraper import *
from scripts.alergies import *
import json

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxgzF3slazWjdodJZiAdtous_KOGOTKnIXqoXmsRMaX7QM5AvCzP6tHiuListDrBm9P/exec"

def quali_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event):
    # 1. Initialize Driver with a safety try block
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Initializing scraper..."}))
        
        driver, credentials = init_scraper(user_id, db_session)
        
        # Set a hard page load limit (e.g., 50 seconds)
        driver.set_page_load_timeout(50)

        # 2. Check stop_event before starting heavy tasks
        if stop_event.is_set(): return

        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
        
        if stop_event.is_set(): return

        cadetNames, numberOfCadets = get_cadet_names(driver)
        
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {numberOfCadets} cadets. Fetching qualifications..."}))

        # 3. Pass stop_event into the data collection function
        # You will need to update the definition of get_cadet_qualifications to accept this argument
        cadet_quali_data = get_cadet_qualifications(
            driver, 
            cadetNames, 
            numberOfCadets, 
            scraper_messages, 
            scraper_lock, 
        )
        
        # 4. Only push to Google if we weren't stopped
        if not stop_event.is_set():
            push_to_google_apps_script({"cadet_quali": cadet_quali_data}, APPS_SCRIPT_URL, scraper_messages, scraper_lock)
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "status", "value": "Scraper completed successfully!"}))
        else:
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped by timeout."}))

    except TimeoutException:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": "A page took too long to load (Timeout)."}))
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Scraper Error: {str(e)}"}))
    finally:
        # 5. ALWAYS close the driver to free up system memory
        if driver:
            driver.quit()

def cadet_event_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started cadet event scraper"}))
        
        # 1. Initialize and set timeout
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)

        # 2. Login with stop check
        if stop_event.is_set(): return
        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        # 3. Get initial links
        if stop_event.is_set(): return
        event_names, number_of_events, event_links_317 = get_event_names_and_317_links(driver)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Got event names, starting to get cadets on events"}))

        # 4. Get Attendees (Ensure this function is updated to accept and check stop_event)
        event_attendees = get_event_attendees(
            driver, 
            event_names, 
            number_of_events, 
            scraper_messages, 
            scraper_lock,
        )
        
        # 5. Push data if not interrupted
        if not stop_event.is_set():
            push_to_google_apps_script({"events": event_attendees}, APPS_SCRIPT_URL, scraper_messages, scraper_lock)
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))
        else:
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped: Timeout reached."}))

    except TimeoutException:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": "Page load timed out."}))
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Internal Error: {str(e)}"}))
    finally:
        # Crucial: Always kill the browser process
        if driver:
            driver.quit()

def event_317_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started 317 event info scraper"}))
        
        # 1. Initialize Driver & Set Safety Timeout
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50) # Stop individual pages from hanging > 50s

        # 2. Login Check
        if stop_event.is_set(): return
        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        # 3. Fetch Event Links
        if stop_event.is_set(): return
        event_names, number_of_events, event_links_317 = get_event_names_and_317_links(driver)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {len(event_links_317)} event links. Syncing to database..."}))

        # 4. Sync Info (Ensure get_317_event_info accepts stop_event)
        # This function should check stop_event.is_set() inside its loop through event_links_317
        get_317_event_info(
            driver, 
            event_links_317, 
            scraper_messages, 
            scraper_lock, 
        )

        # 5. Final Status Update
        with scraper_lock:
            if stop_event.is_set():
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped: Time limit exceeded."}))
            else:
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except TimeoutException:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": "Bader took too long to respond. Connection timed out."}))
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Sync Error: {str(e)}"}))
    finally:
        # 6. Safety Cleanup
        if driver:
            driver.quit()

def check_banned_scraper():
    pass
    # banned_and_bidding = get_event_bans(event_data)

def medical_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started medical and dietary scraper"}))
        
        # 1. Initialize and set page timeout
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)

        # 2. Login with stop check
        if stop_event.is_set(): return
        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        # 3. Get Cadet List
        if stop_event.is_set(): return
        cadetNames, numberOfCadets = get_cadet_names(driver)

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info", 
                "value": f"Got {numberOfCadets} cadets, starting to fetch allergies and dietary requirements"
            }))

        # 4. Fetch Medical Data (Update get_cadet_medical to accept stop_event)
        cadet_allergies_data = get_cadet_medical(
            driver, 
            cadetNames, 
            numberOfCadets, 
            scraper_messages, 
            scraper_lock,
        )

        # 5. Push to Apps Script if not interrupted
        if not stop_event.is_set():
            push_to_google_apps_script(
                cadet_allergies_data, 
                "https://script.google.com/macros/s/AKfycbxl94R1lBUwx4R2yu3Bzi82GEvPk6tpDVNE1EW065STdDUBYEDrC2ItdpfidcuRPwBg/exec", 
                scraper_messages, 
                scraper_lock
            )
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))
        else:
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped: Timeout reached during medical sync."}))

    except TimeoutException:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": "Connection timed out while loading cadet profiles."}))
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Medical Scraper Error: {str(e)}"}))
    finally:
        # Cleanup browser resources
        if driver:
            driver.quit()
