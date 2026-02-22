from scraper_utils import *

# Get cadet qualifications
# run_scraper()

# Cadets on events + ban notification checker

# Sqn events for AO generator

from database.database import Base, engine, SessionLocal
from database.models import Cadet, Location, Event317, AllEvent, CadetEvent, BanNotification

# make sure every table is created
Base.metadata.create_all(bind=engine)

credentials = load_credentials()
driver = init_driver()

try:
    login(driver, credentials)


    event_names, numberOfEvents, event_links_317 = get_event_names_and_317_links(driver)
    # event_data = get_event_attendees(driver, event_names, numberOfEvents)
    get_317_event_info(driver, event_links_317)

finally:
    driver.quit()
