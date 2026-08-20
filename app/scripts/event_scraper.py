from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from database.database import SessionLocal
from database.models import Location, Event317

from scripts.tables import ensure_all_rows_shown
from scripts.waiter import wait_for_aspx_load, wait_for_preloader, safe_click

# Unit label the events table uses for our squadron — the marker for which rows
# get their full event details pulled as well as their attendees.
UNIT_317 = "317 (Failsworth & Newton Heath)"
EVENT_DETAIL_URL = "https://sms.bader.mod.uk/events/details/detail.aspx?eventId={}"
EVENTS_TABLE_URL = "https://sms.bader.mod.uk/events/default.aspx"
# The 317 detail pages are plain GETs of server-rendered WebForms fields, so
# they're fetched over HTTP rather than in the browser. Kept small: the win is
# hiding Bader's latency, not saturating it.
DETAIL_FETCH_WORKERS = 5


def clean_html(raw_html):
    if not raw_html:
        return None
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def _ensure_all_rows_shown(page: Page, expected_rows: int | None = None):
    """Put the table back to "show all rows", but only when it isn't already.

    The shared helper is what this function used to be; the other scrapers now
    lean on the same idempotency, so it lives in scripts.tables.
    """
    ensure_all_rows_shown(page, "eventTable_length", "eventTable", expected_rows)


def _ensure_events_table(page: Page, expected_rows: int | None = None):
    """Get back to a fully rendered events table as cheaply as possible.

    Closing a modal leaves us on the events page with the table and its filters
    intact, so only an actual navigation away (sub-app scraping) needs the full
    ``_setup_events_table`` round-trip.
    """
    if "events/default.aspx" not in (page.url or "").lower():
        _setup_events_table(page)
        return
    _ensure_all_rows_shown(page, expected_rows)


def _setup_events_table(page: Page):
    page.goto(EVENTS_TABLE_URL)
    wait_for_aspx_load(page)

    # The evaluate() calls below throw if the control isn't in the DOM yet; wait
    # for the first one instead of sleeping a fixed 2s and hoping.
    page.wait_for_selector(
        "[name='ctl00$ctl00$cphBaseBody$cphBody$cbAdultIC']", timeout=20000
    )

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

    _ensure_all_rows_shown(page)


def _get_table_rows(page: Page):
    tbodies = page.query_selector_all("tbody")
    if not tbodies:
        raise Exception("Events table not found on page")
    rows = tbodies[0].query_selector_all("tr")
    if not rows:
        raise Exception("No events found in the table")
    return rows


def _event_id_from_row(columns):
    """Event id off the title cell's detail link, or None if the row has none.

    The first anchor in the cell is the share dropdown (href="#"), the second is
    the actual detail link, so prefer the second and fall back to the first.
    """
    links = columns[1].query_selector_all("a") if len(columns) > 1 else []
    if not links:
        return None
    href = links[1].get_attribute("href") if len(links) > 1 else links[0].get_attribute("href")
    if href and "eventId=" in href:
        return href.split("eventId=")[1].split("&")[0]
    return None


def get_event_names_and_317_links(page: Page):
    """One walk of the events table: every event name, plus detail links for the
    317 events so their metadata sync can ride along with the same scrape.

    Links are built absolute from the event id rather than taken from the href
    attribute, which is relative (``details\\detail.aspx?eventId=...``).
    """
    _setup_events_table(page)
    rows = _get_table_rows(page)
    event_names = []
    event_links_317 = []
    for row in rows:
        columns = row.query_selector_all("td")
        if len(columns) < 7:
            raise Exception("Unexpected table format, not enough columns")
        event_names.append(columns[1].inner_text().replace("\n", " "))

        if columns[6].inner_text().strip() != UNIT_317:
            continue
        event_id = _event_id_from_row(columns)
        if event_id:
            event_links_317.append(EVENT_DETAIL_URL.format(event_id))

    info_el = page.wait_for_selector("#eventTable_info", timeout=20000)
    try:
        number_of_events = int(info_el.inner_text().split(" ")[5])
    except (IndexError, ValueError):
        raise Exception(f"Failed to parse number of events from text: '{info_el.inner_text()}'")

    return event_names, number_of_events, event_links_317


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
    # get_event_names_and_317_links has just built this table and nothing has
    # navigated since, so this is normally a no-op rather than a full rebuild.
    _ensure_events_table(page, number_of_events)

    event_attendees = []
    for i in range(number_of_events):
        if stop_event and stop_event.is_set():
            return event_attendees
        with scraper_lock:
            scraper_messages.append(f"On event number {i+1} out of {number_of_events}")
        try:
            wait_for_preloader(page)
            wait_for_aspx_load(page)
            _ensure_all_rows_shown(page, number_of_events)

            event_id = None
            try:
                rows = _get_table_rows(page)
                event_id = _event_id_from_row(rows[i].query_selector_all("td"))
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
                _ensure_events_table(page, number_of_events)
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
                _ensure_events_table(page, number_of_events)
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
                _ensure_events_table(page, number_of_events)
            else:
                event_attendees[-1]["attendees"] = [
                    [col.inner_text() for col in row.query_selector_all("td")]
                    for row in rows
                ]
                safe_click(page, close_button)

        except Exception:
            event_attendees[-1]["attendees"] = "No cadets attending this event."

    return event_attendees


# Fields pulled off an event detail page, in the order the form lays them out.
# Keys are the Event317 kwargs; values are the label each one sits under.
DETAIL_FIELDS = {
    "title":          "Title",
    "reference":      "Reference",
    "adult_ic":       "Adult IC",
    "date_from":      "Date From",
    "date_to":        "Date To",
    "contact_number": "Contact No.",
    "location_name":  "Location",
    "postcode":       "Postcode",
    "cost":           "Cost Per Cadet",
    "dress":          "Dress",
}


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


def _read_detail_from_page(page: Page) -> dict:
    """Read the detail fields by driving the browser. The slow, always-correct path."""
    fields = {key: _get_input_text(page, label) for key, label in DETAIL_FIELDS.items()}
    fields["description"] = clean_html(_get_textarea(page, "Description"))
    return fields


def _soup_field(soup, label_text: str, tag: str) -> str:
    """BeautifulSoup equivalent of //label[contains(., X)]/following::<tag>[1].

    ``find_next`` walks document order, so this picks the same element the
    Playwright xpath would, off the first matching label on the page.
    """
    label = next(
        (l for l in soup.find_all("label") if label_text in l.get_text()),
        None,
    )
    if not label:
        return ""
    el = label.find_next(tag)
    if el is None:
        return ""
    # An input carries its value as an attribute; a textarea carries it as text,
    # and get_text() unescapes entities the way el.value would.
    value = el.get("value", "") if tag == "input" else el.get_text()
    return (value or "").strip()


def _parse_detail_html(html: str) -> dict | None:
    """Read the detail fields straight out of the server-rendered HTML.

    Returns None whenever the read looks incomplete, which sends that event to
    the browser fallback instead. Two ways it can be incomplete: a timed-out
    session bounces to the login form, and any field a datepicker or other script
    fills in after load has no server-rendered value attribute for us to read.
    Title and Date From are mandatory on an SMS event, so a blank one of those
    means we are not looking at the whole page.
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(attrs={"name": "txtUsername"}):
        return None

    fields = {key: _soup_field(soup, label, "input") for key, label in DETAIL_FIELDS.items()}
    if not fields["title"] or not fields["date_from"]:
        return None
    fields["description"] = clean_html(_soup_field(soup, "Description", "textarea"))
    return fields


def _fetch_detail_pages(page: Page, links, scraper_messages, scraper_lock) -> dict:
    """Fetch the event detail pages over HTTP rather than in the browser.

    Every field we want is rendered server-side by WebForms, so a renderer
    process buys us nothing — this reuses the already authenticated session
    cookies and fans the requests out over a small thread pool, which hides
    Bader's per-page latency without costing any extra browser memory.

    Returns {url: html} for whatever came back; anything missing or unparseable
    falls back to a real page load at the call site.
    """
    if not links:
        return {}

    try:
        cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    except Exception:
        return {}

    headers = {}
    try:
        headers["User-Agent"] = page.evaluate("() => navigator.userAgent")
    except Exception:
        pass

    fetched = {}
    failures = 0
    try:
        with httpx.Client(cookies=cookies, headers=headers, timeout=30.0, follow_redirects=True) as client:
            with ThreadPoolExecutor(max_workers=min(DETAIL_FETCH_WORKERS, len(links))) as pool:
                futures = {pool.submit(client.get, link): link for link in links}
                for future in as_completed(futures):
                    link = futures[future]
                    try:
                        response = future.result()
                        if response.status_code == 200:
                            fetched[link] = response.text
                        else:
                            failures += 1
                    except Exception:
                        failures += 1
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(f"HTTP fetch of event details unavailable ({e}), using the browser instead.")
        return {}

    if failures:
        with scraper_lock:
            scraper_messages.append(f"{failures} event page(s) did not fetch over HTTP, will load them in the browser.")
    return fetched


def get_317_event_info(page: Page, event_links_317, scraper_messages, scraper_lock, stop_event=None):
    def parse_date(d):
        if not d:
            return None
        try:
            return datetime.strptime(d, "%d/%m/%Y %H:%M")
        except ValueError:
            return None

    session = SessionLocal()
    try:
        num_links = len(event_links_317)
        fetched = _fetch_detail_pages(page, event_links_317, scraper_messages, scraper_lock)

        parsed = {}
        for link, html in fetched.items():
            fields = _parse_detail_html(html)
            if fields:
                parsed[link] = fields

        with scraper_lock:
            scraper_messages.append(
                f"Read {len(parsed)} of {num_links} event page(s) over HTTP; "
                f"{num_links - len(parsed)} need the browser."
            )

        # Only clear the table once we know we have something to put back, so a
        # failed fetch can't leave the events list empty.
        session.query(Event317).delete()
        session.commit()

        for index, link in enumerate(event_links_317):
            if stop_event and stop_event.is_set():
                break

            fields = parsed.get(link)
            if fields is None:
                with scraper_lock:
                    scraper_messages.append(f"On 317 event {index+1} out of {num_links} (browser)")
                page.goto(link)
                wait_for_preloader(page)
                wait_for_aspx_load(page)
                page.wait_for_selector("body", timeout=10000)
                fields = _read_detail_from_page(page)

            location_name = fields["location_name"]
            postcode = fields["postcode"]
            contact_number = fields["contact_number"]

            try:
                cost_int = int(float(fields["cost"]))
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
                title=fields["title"] or "Untitled",
                reference=fields["reference"] or "Error",
                adult_ic=fields["adult_ic"] or "N/A",
                contact_number=int(contact_number) if contact_number and contact_number.isdigit() else 0,
                date_from=parse_date(fields["date_from"]),
                date_to=parse_date(fields["date_to"]),
                location_id=location.id,
                cost=cost_int,
                dress=fields["dress"] or "Unknown",
                description=fields["description"] or "Unknown",
            )
            session.add(event)
            session.commit()

    except Exception as e:
        session.rollback()
        print(f"Error during event sync: {e}")
        raise
    finally:
        session.close()
