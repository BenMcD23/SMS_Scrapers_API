import os
import json
import threading
import requests
from dotenv import load_dotenv

import psutil
from playwright.sync_api import sync_playwright, BrowserContext, Page

from database.models import User
from utils.crypto import decrypt_password
from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click

MIN_FREE_RAM_MB = 500


def check_ram_ok() -> tuple[bool, float]:
    available = psutil.virtual_memory().available / (1024 * 1024)
    return available >= MIN_FREE_RAM_MB, available


class BrowserPool:
    _instance = None
    _class_lock = threading.Lock()

    def __new__(cls):
        with cls._class_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._lock = threading.Lock()
                inst._playwright = None
                inst._browser = None
                cls._instance = inst
        return cls._instance

    def new_context(self) -> BrowserContext:
        with self._lock:
            self._ensure_running()
            return self._browser.new_context(viewport={"width": 1920, "height": 1080})

    def _ensure_running(self):
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-features=site-per-process",
                    "--renderer-process-limit=4",
                ],
            )


_pool = BrowserPool()


def init_driver() -> tuple[BrowserContext, Page]:
    context = _pool.new_context()
    page = context.new_page()
    return context, page


def login(page: Page, credentials: dict, scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append(json.dumps({"type": "info", "value": "Attempting login"}))

    page.goto("https://sms.bader.mod.uk/")
    wait_for_aspx_load(page)

    page.fill("[name='txtUsername']", credentials["role_username"])
    page.fill("[name='txtPassword']", credentials["role_password"])
    page.fill("[name='txtSecondaryUsername']", credentials["personal_username"])
    page.fill("[name='txtSecondaryPassword']", credentials["personal_password"])

    login_btn = page.wait_for_selector("[name='btnSubmit']", timeout=20000)
    safe_click(page, login_btn)

    wait_for_preloader(page)
    wait_for_aspx_load(page)

    if page.url != "https://sms.bader.mod.uk/default.aspx":
        raise Exception(f"Login failed, current URL: {page.url}")

    with scraper_lock:
        scraper_messages.append(json.dumps({"type": "info", "value": "Logged in"}))


def init_scraper(user_id, db_session) -> tuple[Page, BrowserContext, dict]:
    user = db_session.query(User).filter(User.id == user_id).first()

    if not user or not user.bader_credentials or not user.bader_credentials.role_password:
        raise Exception("No credentials found!")

    creds = user.bader_credentials
    credentials = {
        "role_username": creds.role_username,
        "role_password": decrypt_password(creds.role_password),
        "personal_username": creds.personal_username,
        "personal_password": decrypt_password(creds.personal_password),
    }

    context, page = init_driver()
    return page, context, credentials


def push_to_google_apps_script(data, url, scraper_messages, scraper_lock):
    with scraper_lock:
        scraper_messages.append("Pushing data to sheets")

    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=data, headers=headers)

    if response.status_code == 200:
        with scraper_lock:
            scraper_messages.append(f"Data pushed successfully: {response.text}")
    else:
        with scraper_lock:
            scraper_messages.append(f"Failed to push data: {response.status_code}, {response.text}")
