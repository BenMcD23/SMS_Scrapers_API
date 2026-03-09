from scripts.scraper_utils import init_scraper, push_to_google_apps_script, login
from scripts.quali_scraper import *
from scripts.event_scraper import *
from scripts.alergies import *
import json

from database.models import Cadet, CadetQualification
from google_admin_api.get_all_users import get_workspace_users

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxgzF3slazWjdodJZiAdtous_KOGOTKnIXqoXmsRMaX7QM5AvCzP6tHiuListDrBm9P/exec"

def info_and_quali_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Initializing scraper..."}))

        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)

        if stop_event.is_set(): return

        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return

        cadetNames, numberOfCadets = get_cadet_names(driver)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {numberOfCadets} cadets. Fetching info and qualifications..."}))

        cadet_data = get_cadet_info_and_qualifications(
            driver,
            cadetNames,
            numberOfCadets,
            scraper_messages,
            scraper_lock,
        )

        if stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped by timeout."}))
            return

        # ── Fetch workspace emails and build lookup ────────────────────────────
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Fetching Google Workspace emails..."}))
        try:
            workspace_users = get_workspace_users()
            # Key: (first_name_upper, last_name_upper) → email
            # uppercase keys for comparison, but the dict value retains original
            email_map = {
                (u["first_name_key"], u["last_name_key"]): u["email"]
                for u in workspace_users
            }
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "info", "value": f"Fetched {len(workspace_users)} workspace accounts."}))
        except Exception as e:
            email_map = {}
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "warning", "value": f"Could not fetch workspace emails: {str(e)}. Continuing without emails."}))

        # ── Upsert into DB ────────────────────────────────────────────────────
        saved = 0
        skipped = 0
        emails_matched = 0

        for entry in cadet_data:
            cin = entry.get("cin")

            if not cin:
                print(f"[Scraper] Skipping {entry.get('first_name')} {entry.get('last_name')} — no CIN found")
                skipped += 1
                continue

            try:
                cin = int(cin)
            except (ValueError, TypeError):
                print(f"[Scraper] Skipping {entry.get('first_name')} {entry.get('last_name')} — CIN '{cin}' is not a valid integer")
                skipped += 1
                continue

            # Uppercase only for comparison — names saved to DB come from SMS as-is
            first_key = (entry.get("first_name") or "").strip().upper()
            last_key  = (entry.get("last_name")  or "").strip().upper()

            # 1. Full first + last
            email = email_map.get((first_key, last_key))
            if email:
                print(f"[Scraper] Matched '{first_key}' '{last_key}' by full name → {email}")
            else:
                # 2. First initial + last  (e.g. 'T' 'SMITH')
                initial_key = first_key[0] if first_key else ""
                email = next(
                    (v for (f, l), v in email_map.items() if l == last_key and f.startswith(initial_key)),
                    None
                )
                if not email:
                    # 3. Last name only — only use if exactly one workspace account shares that surname
                    last_matches = [(f, l, v) for (f, l), v in email_map.items() if l == last_key]
                    if len(last_matches) == 1:
                        email = last_matches[0][2]


            if email:
                emails_matched += 1

            cadet = db_session.query(Cadet).filter(Cadet.cin == cin).first()
            if not cadet:
                cadet = Cadet(cin=cin)
                db_session.add(cadet)

            # Store names exactly as SMS returned them, not uppercased
            cadet.first_name    = entry.get("first_name") or cadet.first_name
            cadet.last_name     = entry.get("last_name")  or cadet.last_name
            cadet.rank          = entry.get("rank")        or cadet.rank
            cadet.flight        = entry.get("flight")      or cadet.flight
            cadet.date_of_birth = entry.get("date_of_birth") or cadet.date_of_birth
            cadet.email         = email or cadet.email  # keep existing if no match

            # Upsert qualifications
            existing_quals = {cq.qual_type for cq in cadet.qualifications}
            for qual in entry.get("qualifications", []):
                if isinstance(qual, str):
                    qual_type     = qual
                    status        = "true"
                    date_achieved = None
                else:
                    qual_type     = qual.get("qual_type")
                    status        = qual.get("status", "true")
                    date_achieved = qual.get("date_achieved")

                if qual_type and qual_type not in existing_quals:
                    db_session.add(CadetQualification(
                        cadet_id=cin,
                        qual_type=qual_type,
                        status=status,
                        date_achieved=date_achieved,
                    ))

            saved += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"DB update complete — {saved} cadets saved, {emails_matched} emails matched, {skipped} skipped."
            }))
            scraper_messages.append(json.dumps({"type": "status", "value": "Scraper completed successfully!"}))

    except TimeoutException:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": "A page took too long to load (Timeout)."}))
    except Exception as e:
        db_session.rollback()
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Scraper Error: {str(e)}"}))
    finally:
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
