from scripts.scraper_utils import init_scraper, push_to_google_apps_script, login
from scripts.quali_scraper import *
from scripts.event_scraper import *
from scripts.alergies import *
from scripts.add_quali import add_qualification_with_attachment

import json

from database.models import Cadet, CadetQualification, AllEvent, CadetEvent
from google_admin_api.get_all_users import get_workspace_users

import os
import tempfile

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxgzF3slazWjdodJZiAdtous_KOGOTKnIXqoXmsRMaX7QM5AvCzP6tHiuListDrBm9P/exec"

def info_and_quali_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_driver_ready=None):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Initializing scraper..."}))

        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)
        if on_driver_ready:
            on_driver_ready(driver)

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
            stop_event=stop_event,
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
                scraper_messages.append(json.dumps({"type": "warning", "value": f"[WARN] Could not fetch workspace emails: {str(e)}. Continuing without emails."}))

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
            if not email:
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

            # Deduplicate scraped qualifications by qual_type, keeping most recent date_achieved
            deduped_quals = {}
            for qual in entry.get("qualifications", []):
                if isinstance(qual, str):
                    qt, st, da, de = qual, "true", None, None
                else:
                    qt = qual.get("qual_type")
                    st = qual.get("status", "true")
                    da = qual.get("date_achieved")
                    de = qual.get("date_expires")

                if not qt:
                    continue

                if qt not in deduped_quals:
                    deduped_quals[qt] = {"qual_type": qt, "status": st, "date_achieved": da, "date_expires": de}
                else:
                    existing_da = deduped_quals[qt]["date_achieved"]
                    if da is not None and (existing_da is None or da > existing_da):
                        deduped_quals[qt] = {"qual_type": qt, "status": st, "date_achieved": da, "date_expires": de}

            # Upsert qualifications — update dates if missing, insert if new
            existing_quals = {cq.qual_type: cq for cq in cadet.qualifications}
            for qt, qual in deduped_quals.items():
                if qt in existing_quals:
                    cq = existing_quals[qt]
                    if qual["date_achieved"] is not None and cq.date_achieved is None:
                        cq.date_achieved = qual["date_achieved"]
                    if qual["date_expires"] is not None and cq.date_expires is None:
                        cq.date_expires = qual["date_expires"]
                else:
                    db_session.add(CadetQualification(
                        cadet_id=cin,
                        qual_type=qt,
                        status=qual["status"],
                        date_achieved=qual["date_achieved"],
                        date_expires=qual["date_expires"],
                    ))

            saved += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"DB update complete — {saved} cadets saved, {emails_matched} emails matched, {skipped} skipped."
            }))
            scraper_messages.append(json.dumps({"type": "status", "value": "Scraper completed successfully!"}))

        apps_script_payload = [
            {
                "cadet_name": f"{entry.get('first_name', '')} {entry.get('last_name', '')}".strip(),
                "qualifications": [
                    q if isinstance(q, str) else q.get("qual_type", "")
                    for q in entry.get("qualifications", [])
                ]
            }
            for entry in cadet_data
        ]
        push_to_google_apps_script({"cadet_quali": apps_script_payload}, APPS_SCRIPT_URL, scraper_messages, scraper_lock)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Scraper completed successfully!"}))
        
    except TimeoutException:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "A page took too long to load (Timeout)."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Scraper Error: {str(e)}"}))
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def cadet_event_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_driver_ready=None):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started cadet event scraper"}))
        
        # 1. Initialize and set timeout
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)
        if on_driver_ready:
            on_driver_ready(driver)

        # 2. Login with stop check
        if stop_event.is_set(): return
        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        # 3. Get all event names and count
        if stop_event.is_set(): return
        event_names, number_of_events = get_all_event_names(driver)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Got event names, starting to get cadets on events"}))

        # 4. Get Attendees
        event_attendees = get_event_attendees(
            driver,
            event_names,
            number_of_events,
            scraper_messages,
            scraper_lock,
            stop_event=stop_event,
        )
        
        if stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped: Timeout reached."}))
            return

        # 5. Save events and attendees to DB
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Saving events to database..."}))

        # Clear existing data so we get a fresh sync each run
        db_session.query(CadetEvent).delete()
        db_session.query(AllEvent).delete()
        db_session.commit()

        saved_events = 0
        unmatched_cadets = 0

        # Build a name lookup: (first_upper, last_upper) -> cin
        all_cadets = db_session.query(Cadet).all()
        cadet_lookup = {
            (c.first_name.strip().upper(), c.last_name.strip().upper()): c.cin
            for c in all_cadets
        }

        def _save_cadet_events(event_db_id, attendees, matched_ref, unmatched_ref):
            for row in attendees:
                if not isinstance(row, list) or len(row) < 3:
                    continue
                first = row[1].strip().upper()
                last  = row[2].strip().upper()
                cin = cadet_lookup.get((first, last))
                if not cin:
                    initial = first[0] if first else ""
                    cin = next(
                        (v for (f, l), v in cadet_lookup.items() if l == last and f.startswith(initial)),
                        None,
                    )
                if cin:
                    db_session.add(CadetEvent(event_id=event_db_id, cadet_id=cin))
                    matched_ref[0] += 1
                else:
                    unmatched_ref[0] += 1

        matched_ref   = [0]
        unmatched_ref = [0]

        for entry in event_attendees:
            attendees = entry.get("attendees")
            sub_apps  = entry.get("sub_apps", [])
            has_direct = isinstance(attendees, list)
            has_subs   = any(isinstance(s.get("attendees"), list) for s in sub_apps)

            if not has_direct and not has_subs:
                continue

            parent_event = AllEvent(title=entry["event_name"], parent_id=None)
            db_session.add(parent_event)
            db_session.flush()

            if has_direct:
                _save_cadet_events(parent_event.id, attendees, matched_ref, unmatched_ref)
                saved_events += 1

            for sub in sub_apps:
                sub_attendees = sub.get("attendees")
                if not isinstance(sub_attendees, list):
                    continue
                sub_event = AllEvent(title=sub["sub_app_name"], parent_id=parent_event.id)
                db_session.add(sub_event)
                db_session.flush()
                _save_cadet_events(sub_event.id, sub_attendees, matched_ref, unmatched_ref)
                saved_events += 1

        matched_cadets   = matched_ref[0]
        unmatched_cadets = unmatched_ref[0]
        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": (
                    f"Saved {saved_events} event(s). "
                    f"{matched_cadets} cadet attendance(s) matched. "
                    + (f"{unmatched_cadets} attendee(s) could not be matched to a cadet." if unmatched_cadets else "All attendees matched successfully.")
                ),
            }))
            scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except TimeoutException:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Page load timed out."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Internal Error: {str(e)}"}))
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def event_317_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_driver_ready=None):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started 317 event info scraper"}))

        # 1. Initialize Driver & Set Safety Timeout
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)
        if on_driver_ready:
            on_driver_ready(driver)

        # 2. Login Check
        if stop_event.is_set(): return
        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        # 3. Fetch 317 event links
        if stop_event.is_set(): return
        event_links_317 = get_317_event_links(driver)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {len(event_links_317)} event links. Syncing to database..."}))

        # 4. Sync Info
        get_317_event_info(
            driver,
            event_links_317,
            scraper_messages,
            scraper_lock,
            stop_event=stop_event,
        )

        # 5. Final Status Update
        with scraper_lock:
            if not stop_event.is_set():
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except TimeoutException:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Bader took too long to respond. Connection timed out."}))
    except Exception as e:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Sync Error: {str(e)}"}))
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def check_banned_scraper():
    pass
    # banned_and_bidding = get_event_bans(event_data)

def medical_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_driver_ready=None):
    driver = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started medical and dietary scraper"}))

        # 1. Initialize and set page timeout
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)
        if on_driver_ready:
            on_driver_ready(driver)

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

        # 4. Fetch Medical Data
        cadet_allergies_data = get_cadet_medical(
            driver,
            cadetNames,
            numberOfCadets,
            scraper_messages,
            scraper_lock,
            stop_event=stop_event,
        )

        # 5. Push to Apps Script if not stopped
        if not stop_event.is_set():
            push_to_google_apps_script(
                cadet_allergies_data,
                "https://script.google.com/macros/s/AKfycbxl94R1lBUwx4R2yu3Bzi82GEvPk6tpDVNE1EW065STdDUBYEDrC2ItdpfidcuRPwBg/exec",
                scraper_messages,
                scraper_lock
            )
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except TimeoutException:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Connection timed out while loading cadet profiles."}))
    except Exception as e:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Medical Scraper Error: {str(e)}"}))
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass




# Map assessment_type → exact qualification name as it appears in the Bader dropdown
ASSESSMENT_TYPE_TO_QUAL_NAME: dict[str, str] = {
    "Blue Leadership": "Blue Leadership",
    "first_aid":       "First Aid",
    "radio":           "Radio Operator",
}


def upload_qualifications_scraper(
    scraper_messages,
    scraper_lock,
    user_id: int,
    db_session,
    stop_event,
    assessment_ids: list[int],
):
    driver = None

    def log(msg: str, level: str = "info"):
        payload = json.dumps({"type": level, "value": msg})
        if scraper_messages is not None and scraper_lock is not None:
            with scraper_lock:
                scraper_messages.append(payload)
        else:
            print(msg)

    try:
        driver, credentials = init_scraper(user_id, db_session)
        driver.set_page_load_timeout(50)

        if stop_event.is_set():
            return

        login(driver, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
        log("✓ Logged in to Bader SMS.")

        # ── Load all requested sheets ─────────────────────────────────────────
        from database.models import AssessmentSheet
        sheets = (
            db_session.query(AssessmentSheet)
            .filter(AssessmentSheet.id.in_(assessment_ids))
            .all()
        )

        # ── Group by (cadet_id, assessment_type) ──────────────────────────────
        # We want one add_qualification_with_attachment call per cadet+type,
        # passing ALL the PDFs for that group at once.
        from collections import defaultdict
        groups: dict[tuple[int, str], list[AssessmentSheet]] = defaultdict(list)
        for sheet in sheets:
            groups[(sheet.cadet_id, sheet.assessment_type)].append(sheet)

        total_groups = len(groups)
        log(f"Processing {total_groups} qualification group(s)...")

        for group_num, ((cadet_id, assessment_type), group_sheets) in enumerate(groups.items(), 1):
            if stop_event.is_set():
                log("Upload stopped by timeout.", "error")
                return

            # ── Validate ──────────────────────────────────────────────────────
            cadet = group_sheets[0].cadet
            if not cadet:
                log(f"No cadet linked to assessment group (cadet_id={cadet_id}) — skipping.", "warning")
                continue

            qual_name = ASSESSMENT_TYPE_TO_QUAL_NAME.get(assessment_type)
            if not qual_name:
                log(
                    f"No Bader qualification mapped for type '{assessment_type}' "
                    f"(cadet {cadet_id}) — skipping.",
                    "warning",
                )
                continue

            sheets_with_pdf = [s for s in group_sheets if s.pdf_data]
            if not sheets_with_pdf:
                log(
                    f"No PDFs stored for {assessment_type} / CIN {cadet.cin} — skipping.",
                    "warning",
                )
                continue

            log(
                f"[{group_num}/{total_groups}] Uploading '{qual_name}' for "
                f"{cadet.first_name} {cadet.last_name} (CIN {cadet.cin}) — "
                f"{len(sheets_with_pdf)} PDF(s)..."
            )

            # ── Write each PDF to a temp file ─────────────────────────────────
            tmp_paths = []
            try:
                for s in sheets_with_pdf:
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        suffix=".pdf",
                        prefix=f"assessment_{s.id}_",
                    )
                    os.close(tmp_fd)
                    with open(tmp_path, "wb") as f:
                        f.write(s.pdf_data)
                    tmp_paths.append(tmp_path)

                # ── Call the Bader scraper ────────────────────────────────────
                add_qualification_with_attachment(
                    driver=driver,
                    cadet_cin=cadet.cin,
                    qualification_name=qual_name,
                    pdf_paths=tmp_paths,
                    scraper_messages=scraper_messages,
                    scraper_lock=scraper_lock,
                )

            except Exception as qual_err:
                log(
                    f"Failed to upload '{qual_name}' for CIN {cadet.cin}: {qual_err}",
                    "error",
                )
                raise  # stop the whole job on hard failure

            finally:
                for p in tmp_paths:
                    # File may have been renamed inside the scraper, clean up both
                    if os.path.exists(p):
                        os.remove(p)
                    safe_qual_name = qual_name.replace(" ", "_")
                    for j in range(len(tmp_paths)):
                        renamed = os.path.join(
                            os.path.dirname(p),
                            f"{safe_qual_name}_Assessment_{j + 1}.pdf"
                        )
                        if os.path.exists(renamed):
                            os.remove(renamed)
                            
            # ── Mark all sheets in this group as uploaded ─────────────────────
            for s in group_sheets:
                s.uploaded = True
            db_session.commit()

            log(
                f"✓ '{qual_name}' uploaded for "
                f"{cadet.first_name} {cadet.last_name} (CIN {cadet.cin})."
            )

        log("All qualifications processed.", "status")

    except Exception as e:
        db_session.rollback()
        log(f"Upload scraper error: {e}", "error")
    finally:
        if driver:
            driver.quit()