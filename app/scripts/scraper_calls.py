from scripts.scraper_utils import init_scraper, push_to_google_apps_script, login
from scripts.quali_scraper import *
from scripts.event_scraper import *
from scripts.alergies import *

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxgzF3slazWjdodJZiAdtous_KOGOTKnIXqoXmsRMaX7QM5AvCzP6tHiuListDrBm9P/exec"

def quali_scraper(scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append("Started scraper")
    driver, credentials = init_scraper()
    login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
    cadetNames, numberOfCadets = get_cadet_names(driver)
    with scraper_lock:
        scraper_messages.append(f"Got {numberOfCadets}, starting to get qualifications")
    cadet_quali_data = get_cadet_qualifications(driver, cadetNames, numberOfCadets, scraper_messages, scraper_lock)
    
    push_to_google_apps_script({"cadet_quali": cadet_quali_data}, APPS_SCRIPT_URL, scraper_messages, scraper_lock)

    with scraper_lock:
        scraper_messages.append("Scraper completed successfully!")

    driver.quit()

def cadet_event_scraper(scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append("Started scraper")
    driver, credentials = init_scraper()
    login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
    event_names, number_of_events, event_links_317 = get_event_names_and_317_links(driver)
    with scraper_lock:
        scraper_messages.append("Got event names, starting to get cadets on events")
    event_attendees = get_event_attendees(driver, event_names, number_of_events, scraper_messages, scraper_lock)
    
    push_to_google_apps_script({"events": event_attendees}, APPS_SCRIPT_URL, scraper_messages, scraper_lock)

    with scraper_lock:
        scraper_messages.append("Scraper completed successfully!")

    driver.quit()

def event_317_scraper(scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append("Started scraper")
    driver, credentials = init_scraper()
    login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
    event_names, number_of_events, event_links_317 = get_event_names_and_317_links(driver)
    with scraper_lock:
        scraper_messages.append("Got event links, starting to get all events info")
    # this just puts the event info into the db
    get_317_event_info(driver, event_links_317, scraper_messages, scraper_lock)

    with scraper_lock:
        scraper_messages.append("Scraper completed successfully!")

    driver.quit()

def check_banned_scraper():
    pass
    # banned_and_bidding = get_event_bans(event_data)

def medical_scraper(scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append("Started scraper")
    driver, credentials = init_scraper()
    login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
    cadetNames, numberOfCadets = get_cadet_names(driver)
    with scraper_lock:
        scraper_messages.append(f"Got {numberOfCadets}, starting to get allergies and dietary")
    cadet_allergies_data = get_cadet_medical(driver, cadetNames, numberOfCadets, scraper_messages, scraper_lock)

    push_to_google_apps_script(cadet_allergies_data, "https://script.google.com/macros/s/AKfycbxl94R1lBUwx4R2yu3Bzi82GEvPk6tpDVNE1EW065STdDUBYEDrC2ItdpfidcuRPwBg/exec", scraper_messages, scraper_lock)

    with scraper_lock:
        scraper_messages.append("Scraper completed successfully!")

    driver.quit()
