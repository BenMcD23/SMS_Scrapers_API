import os
import json
import time
import requests
import builtins
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from database.models import User

from utils.crypto import decrypt_password

from scripts.event_scraper import *
from scripts.quali_scraper import *

from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click

import sys

# Print with flush for Docker
# def print(*args):
#     builtins.print(*args, sep=' ', end='\n', file=None, flush=True)

def get_env_path():
    """Returns the path to the .env file sitting next to the EXE or in the project root."""
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller EXE
        return os.path.join(os.path.dirname(sys.executable), '.env')
    else:
        # Running in development (source code)
        return os.path.abspath(".env")
    
# def load_credentials():
#     return {
#         "role_username": os.getenv("role_username"),
#         "role_password": os.getenv("role_password"),
#         "personal_username": os.getenv("personal_username"),
#         "personal_password": os.getenv("personal_password")
#     }

def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument('--remote-debugging-pipe')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    # options.add_argument("--disable-software-rasterizer")
    return webdriver.Chrome(options=options)

def login(driver, credentials, scraper_messages, scraper_lock):
    print("login", scraper_lock, scraper_messages)

    with scraper_lock:
        scraper_messages.append("Attempting login")
    driver.get("https://sms.bader.mod.uk/")

    wait_for_aspx_load(driver)

    driver.find_element(By.NAME, "txtUsername").send_keys(credentials["role_username"])
    driver.find_element(By.NAME, "txtPassword").send_keys(credentials["role_password"])
    driver.find_element(By.NAME, "txtSecondaryUsername").send_keys(credentials["personal_username"])
    driver.find_element(By.NAME, "txtSecondaryPassword").send_keys(credentials["personal_password"])
    login_button = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.NAME, "btnSubmit")))
    safe_click(driver, login_button)

    wait_for_preloader(driver)
    wait_for_aspx_load(driver)
    
    expected_url = "https://sms.bader.mod.uk/default.aspx"
    if driver.current_url != expected_url:
        raise Exception(f"Login failed, current URL: {driver.current_url}")
    
    with scraper_lock:
        scraper_messages.append("Logged in")

def init_scraper(user_id, db_session):
    user = db_session.query(User).filter(User.id == user_id).first()
    
    if not user or not user.bader_credentials or not user.bader_credentials.role_password:
        raise Exception("No credentials found!")

    creds = user.bader_credentials
    credentials = {
        "role_username": creds.role_username,
        "role_password": decrypt_password(creds.role_password),
        "personal_username": creds.personal_username,
        "personal_password": decrypt_password(creds.personal_password)
    }
    
    driver = init_driver()
    driver.set_page_load_timeout(60) 
    driver.set_script_timeout(60)

    return driver, credentials


def push_to_google_apps_script(data, url, scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append("Pushing data to sheets")

    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, json=data, headers=headers)
    
    if response.status_code == 200:
        with scraper_lock:
            scraper_messages.append(f"Data pushed successfully: {response.text}")
    else:
        with scraper_lock:
            scraper_messages.append(f"Failed to push data: {response.status_code}, {response.text}")
