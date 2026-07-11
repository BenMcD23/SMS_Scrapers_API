from scripts.scraper_utils import init_scraper, push_to_google_apps_script, login, match_email
from scripts.quali_scraper import *
from scripts.event_scraper import *
from scripts.alergies import *
from scripts.add_quali import add_qualification_with_attachment
from scripts.absence_scraper import get_absences
from assessment_builders.pdf_utils import merge_pdfs

import json
from datetime import datetime

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from database.models import Cadet, CadetQualification, AllEvent, CadetEvent, CadetMedical, CadetDietary, AttachmentCheckQual, BanNotification, CadetAbsence
from google_admin_api.get_all_users import get_workspace_users
from core.emailer import send_email, ban_alert_email_html
from core.config import BAN_ALERT_EMAIL

import os
import tempfile

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxgzF3slazWjdodJZiAdtous_KOGOTKnIXqoXmsRMaX7QM5AvCzP6tHiuListDrBm9P/exec"


def info_and_quali_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_context_ready=None):
    context = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Initializing scraper..."}))

        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set(): return

        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return

        cadetNames, numberOfCadets = get_cadet_names(page)

        attachment_check_quals = {q.qual_name.casefold() for q in db_session.query(AttachmentCheckQual).all()}

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {numberOfCadets} cadets. Fetching info and qualifications..."}))

        cadet_data = get_cadet_info_and_qualifications(
            page, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=stop_event,
            attachment_check_quals=attachment_check_quals,
        )

        if stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped by timeout."}))
            return

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Fetching Google Workspace emails..."}))
        try:
            workspace_users = get_workspace_users()
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

        saved = 0
        skipped = 0
        emails_matched = 0
        missing_attachments = 0

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

            first_key = (entry.get("first_name") or "").strip().upper()
            last_key = (entry.get("last_name") or "").strip().upper()

            email = match_email(first_key, last_key, email_map)

            if email:
                emails_matched += 1

            cadet = db_session.query(Cadet).filter(Cadet.cin == cin).first()
            if not cadet:
                cadet = Cadet(cin=cin)
                db_session.add(cadet)

            cadet.first_name = entry.get("first_name") or cadet.first_name
            cadet.last_name = entry.get("last_name") or cadet.last_name
            cadet.rank = entry.get("rank") or cadet.rank
            cadet.flight = entry.get("flight") or cadet.flight
            cadet.date_of_birth = entry.get("date_of_birth") or cadet.date_of_birth
            cadet.email = email or cadet.email
            cadet.classification = entry.get("classification") or cadet.classification

            deduped_quals = {}
            for qual in entry.get("qualifications", []):
                if isinstance(qual, str):
                    qt, st, da, de, ha = qual, "true", None, None, None
                else:
                    qt = qual.get("qual_type")
                    st = qual.get("status", "true")
                    da = qual.get("date_achieved")
                    de = qual.get("date_expires")
                    ha = qual.get("has_attachment")
                if not qt:
                    continue
                if qt not in deduped_quals:
                    deduped_quals[qt] = {"qual_type": qt, "status": st, "date_achieved": da, "date_expires": de, "has_attachment": ha}
                else:
                    existing_da = deduped_quals[qt]["date_achieved"]
                    if da is not None and (existing_da is None or da > existing_da):
                        deduped_quals[qt] = {"qual_type": qt, "status": st, "date_achieved": da, "date_expires": de, "has_attachment": ha}

            existing_quals = {cq.qual_type: cq for cq in cadet.qualifications}
            for qt, qual in deduped_quals.items():
                if qt in existing_quals:
                    cq = existing_quals[qt]
                    if qual["date_achieved"] is not None and cq.date_achieved is None:
                        cq.date_achieved = qual["date_achieved"]
                    if qual["date_expires"] is not None and cq.date_expires is None:
                        cq.date_expires = qual["date_expires"]
                    if qual["has_attachment"] is not None:
                        cq.has_attachment = qual["has_attachment"]
                else:
                    db_session.add(CadetQualification(
                        cadet_id=cin, qual_type=qt, status=qual["status"],
                        date_achieved=qual["date_achieved"], date_expires=qual["date_expires"],
                        has_attachment=qual["has_attachment"],
                    ))

                if qual["has_attachment"] is False:
                    missing_attachments += 1
                    with scraper_lock:
                        scraper_messages.append(json.dumps({
                            "type": "warning",
                            "value": f"[MISSING ATTACHMENT] {entry.get('first_name', '')} {entry.get('last_name', '')} — {qt}".strip(),
                        }))

            saved += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"DB update complete — {saved} cadets saved, {emails_matched} emails matched, {skipped} skipped, {missing_attachments} missing attachment(s)."
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

    except PlaywrightTimeoutError:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "A page took too long to load (Timeout)."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Scraper Error: {str(e)}"}))
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass


def absence_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_context_ready=None):
    """Scrape booked absences from Bader and full-replace Cadet_Absences.

    Absences are matched to cadets by name; unmatched rows are logged and
    skipped (nothing to attach them to).
    """
    context = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Initializing scraper..."}))

        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set(): return

        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Fetching absences..."}))

        absences = get_absences(page)

        if stop_event.is_set(): return

        # {(FIRST_UPPER, LAST_UPPER): cin} for name matching (reuses match_email tiers).
        cin_map = {
            (c.first_name.strip().upper(), c.last_name.strip().upper()): c.cin
            for c in db_session.query(Cadet).all()
            if c.first_name and c.last_name
        }

        scraped_at = datetime.now()
        db_session.query(CadetAbsence).delete()  # full replace — Bader only shows current + future

        matched = 0
        unmatched = 0
        for a in absences:
            cin = match_email(a["first_name"].upper(), a["last_name"].upper(), cin_map)
            if not cin:
                unmatched += 1
                with scraper_lock:
                    scraper_messages.append(json.dumps({
                        "type": "warning",
                        "value": f"[NO MATCH] Absence for {a['first_name']} {a['last_name']} — no cadet found",
                    }))
                continue
            db_session.add(CadetAbsence(
                cadet_id=cin,
                date_from=a["date_from"],
                date_to=a["date_to"],
                reason=a["reason"] or None,
                scraped_at=scraped_at,
            ))
            matched += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"Absences saved — {matched} attached, {unmatched} unmatched, {len(absences)} total.",
            }))
            scraper_messages.append(json.dumps({"type": "status", "value": "Scraper completed successfully!"}))

    except PlaywrightTimeoutError:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "A page took too long to load (Timeout)."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Scraper Error: {str(e)}"}))
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass


def check_ban_notifications(db_session):
    """Email staff about any banned cadet found signed up to an event. All new
    (cadet, event) pairs from this run go in a single email; each pair is only
    ever emailed once — already-notified pairs are recorded in Ban_Notifications
    and skipped on future runs. Returns the number of new pairs alerted."""
    banned = db_session.query(Cadet).filter(Cadet.banned == True).all()
    if not banned:
        return 0

    already = {
        (n.cadet_id, n.event_title)
        for n in db_session.query(BanNotification).all()
    }

    new_pairs = []  # (cadet, event_title)
    for c in banned:
        seen = set()
        for ce in c.cadet_events:
            if not ce.event:
                continue
            key = (c.cin, ce.event.title)
            if key in already or key in seen:
                continue
            seen.add(key)
            new_pairs.append((c, ce.event.title))

    if not new_pairs:
        return 0

    rows = [(f"{c.first_name} {c.last_name}", title) for c, title in new_pairs]
    send_email(
        BAN_ALERT_EMAIL,
        f"Banned cadet(s) on events ({len(rows)})",
        ban_alert_email_html(rows),
    )
    # Only record as notified once the send has been attempted, so a pair is
    # never marked without an email having gone out for it.
    now = datetime.now()
    for c, title in new_pairs:
        db_session.add(BanNotification(cadet_id=c.cin, event_title=title, notified_at=now))
    db_session.commit()
    return len(new_pairs)


def cadet_event_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_context_ready=None):
    context = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started cadet event scraper"}))

        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set(): return
        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return
        event_names, number_of_events = get_all_event_names(page)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Got event names, starting to get cadets on events"}))

        event_attendees = get_event_attendees(
            page, event_names, number_of_events, scraper_messages, scraper_lock, stop_event=stop_event,
        )

        if stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped: Timeout reached."}))
            return

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Saving events to database..."}))

        db_session.query(CadetEvent).delete()
        db_session.query(AllEvent).delete()
        db_session.commit()

        saved_events = 0
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
                last = row[2].strip().upper()
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

        matched_ref = [0]
        unmatched_ref = [0]

        for entry in event_attendees:
            attendees = entry.get("attendees")
            sub_apps = entry.get("sub_apps", [])
            has_direct = isinstance(attendees, list)
            has_subs = any(isinstance(s.get("attendees"), list) for s in sub_apps)

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

        matched_cadets = matched_ref[0]
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

        # Alert staff about any banned cadet on an event — never fatal to the scrape.
        try:
            alerted = check_ban_notifications(db_session)
            if alerted:
                with scraper_lock:
                    scraper_messages.append(json.dumps({
                        "type": "warning",
                        "value": f"Emailed staff about {alerted} banned cadet/event sign-up(s).",
                    }))
        except Exception as e:
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({
                    "type": "warning",
                    "value": f"Ban-notification check failed: {str(e)}",
                }))

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except PlaywrightTimeoutError:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Page load timed out."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Internal Error: {str(e)}"}))
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass


def event_317_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_context_ready=None):
    context = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started 317 event info scraper"}))

        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set(): return
        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return
        event_links_317 = get_317_event_links(page)

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": f"Found {len(event_links_317)} event links. Syncing to database..."}))

        get_317_event_info(page, event_links_317, scraper_messages, scraper_lock, stop_event=stop_event)

        with scraper_lock:
            if not stop_event.is_set():
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except PlaywrightTimeoutError:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Bader took too long to respond. Connection timed out."}))
    except Exception as e:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Sync Error: {str(e)}"}))
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass


def check_banned_scraper():
    pass


def medical_scraper(scraper_messages, scraper_lock, user_id, db_session, stop_event, on_context_ready=None):
    context = None
    try:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "Started medical and dietary scraper"}))

        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set(): return
        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)

        if stop_event.is_set(): return
        cadetNames, numberOfCadets = get_cadet_names(page)

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"Got {numberOfCadets} cadets, starting to fetch allergies and dietary requirements"
            }))

        cadet_allergies_data = get_cadet_medical(
            page, cadetNames, numberOfCadets, scraper_messages, scraper_lock, stop_event=stop_event,
        )

        if stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper stopped by timeout."}))
            return

        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "info", "value": "Saving medical and dietary data to database..."}))

        saved = 0
        skipped = 0
        for entry in cadet_allergies_data:
            cin = entry.get("cin")
            if not cin:
                with scraper_lock:
                    scraper_messages.append(json.dumps({"type": "warning", "value": f"[WARN] No CIN for {entry.get('cadet_name')} — skipping DB save."}))
                skipped += 1
                continue

            cadet = db_session.query(Cadet).filter(Cadet.cin == cin).first()
            if not cadet:
                with scraper_lock:
                    scraper_messages.append(json.dumps({"type": "warning", "value": f"[WARN] Cadet CIN {cin} ({entry.get('cadet_name')}) not found in DB — skipping."}))
                skipped += 1
                continue

            db_session.query(CadetMedical).filter(CadetMedical.cadet_id == cin).delete()
            db_session.query(CadetDietary).filter(CadetDietary.cadet_id == cin).delete()

            for allergy in entry.get("allergies", []):
                db_session.add(CadetMedical(
                    cadet_id=cin,
                    allergy_name=allergy.get("allergy", ""),
                    auto_injector=allergy.get("auto_injector", "No"),
                    severity=allergy.get("severity", ""),
                    details=allergy.get("details", ""),
                ))

            for dietary in entry.get("dietary_restrictions", []):
                db_session.add(CadetDietary(
                    cadet_id=cin,
                    name=dietary.get("name", ""),
                    details=dietary.get("details", ""),
                ))

            saved += 1

        db_session.commit()

        with scraper_lock:
            scraper_messages.append(json.dumps({
                "type": "info",
                "value": f"DB update complete — {saved} cadets saved, {skipped} skipped."
            }))

        push_to_google_apps_script(
            cadet_allergies_data,
            "https://script.google.com/macros/s/AKfycbxl94R1lBUwx4R2yu3Bzi82GEvPk6tpDVNE1EW065STdDUBYEDrC2ItdpfidcuRPwBg/exec",
            scraper_messages,
            scraper_lock,
        )
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "status", "value": "done"}))

    except PlaywrightTimeoutError:
        if not stop_event.is_set():
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": "Connection timed out while loading cadet profiles."}))
    except Exception as e:
        if not stop_event.is_set():
            db_session.rollback()
            with scraper_lock:
                scraper_messages.append(json.dumps({"type": "error", "value": f"Medical Scraper Error: {str(e)}"}))
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass




# Map assessment_type → (badge_key, level) in core.qualifications, the single
# source of truth for the Bader dropdown name + option id. bader_quals_for()
# resolves these to BaderQual(name, bader_id); we upload using the first entry.
from core.qualifications import bader_quals_for, BLUE, YES

ASSESSMENT_TYPE_TO_BADGE: dict[str, tuple[str, str]] = {
    "Blue Leadership": ("leadership", BLUE),
    "Blue Radio":      ("radio", BLUE),
    "MOI":             ("moi", YES),
}

# Assessment types where every sheet in the group (plus any lesson plan) is
# merged into ONE PDF before upload, rather than attached as separate files.
MERGE_GROUP_PDF_TYPES = {"MOI"}


def upload_qualifications_scraper(
    scraper_messages,
    scraper_lock,
    user_id: int,
    db_session,
    stop_event,
    assessment_ids: list[int],
    on_context_ready=None,
):
    context = None

    def log(msg: str, level: str = "info"):
        print(f"[upload-to-bader] {level}: {msg}", flush=True)
        payload = json.dumps({"type": level, "value": msg})
        if scraper_messages is not None and scraper_lock is not None:
            with scraper_lock:
                scraper_messages.append(payload)

    try:
        page, context, credentials = init_scraper(user_id, db_session)
        if on_context_ready:
            on_context_ready(context)

        if stop_event.is_set():
            return

        login(page, credentials, scraper_messages=scraper_messages, scraper_lock=scraper_lock)
        log("Logged in to Bader SMS.")

        from database.models import AssessmentSheet
        sheets = (
            db_session.query(AssessmentSheet)
            .filter(AssessmentSheet.id.in_(assessment_ids))
            .all()
        )

        from collections import defaultdict
        groups: dict[tuple[int, str], list] = defaultdict(list)
        for sheet in sheets:
            groups[(sheet.cadet_id, sheet.assessment_type)].append(sheet)

        total_groups = len(groups)
        log(f"Processing {total_groups} qualification group(s)...")

        for group_num, ((cadet_id, assessment_type), group_sheets) in enumerate(groups.items(), 1):
            if stop_event.is_set():
                log("Upload stopped by timeout.", "error")
                return

            cadet = group_sheets[0].cadet
            if not cadet:
                log(f"No cadet linked to assessment group (cadet_id={cadet_id}) — skipping.", "warning")
                continue

            badge = ASSESSMENT_TYPE_TO_BADGE.get(assessment_type)
            quals = bader_quals_for(*badge) if badge else ()
            if not quals:
                log(
                    f"No Bader qualification mapped for type '{assessment_type}' "
                    f"(cadet {cadet_id}) — skipping.",
                    "warning",
                )
                continue
            qual_name = quals[0].name
            qual_id = quals[0].bader_id

            sheets_with_pdf = [s for s in group_sheets if s.pdf_data]
            if not sheets_with_pdf:
                log(f"No PDFs stored for {assessment_type} / CIN {cadet.cin} — skipping.", "warning")
                continue

            merge_into_one = assessment_type in MERGE_GROUP_PDF_TYPES

            # ── Build the PDF(s) to upload ──────────────────────────────────────
            # Most types: one attachment per assessment. Types in
            # MERGE_GROUP_PDF_TYPES (e.g. MOI): every sheet's rendered sheet +
            # lesson plan, oldest first, flattened into a single PDF — Bader
            # ends up with one cumulative file per qualification, not several.
            if merge_into_one:
                ordered = sorted(sheets_with_pdf, key=lambda s: s.created_at)
                blobs: list[bytes | None] = []
                for s in ordered:
                    blobs.append(s.pdf_data)
                    blobs.append(s.lesson_plan_pdf)
                pdf_payloads = [merge_pdfs(blobs)]
                attachment_label = (
                    "Assessment Sheet + Lesson Plan"
                    if any(s.lesson_plan_pdf for s in ordered)
                    else "Assessment Sheet"
                )
            else:
                pdf_payloads = [s.pdf_data for s in sheets_with_pdf]
                attachment_label = "Assessment Sheet"

            log(
                f"[{group_num}/{total_groups}] Uploading '{qual_name}' for "
                f"{cadet.first_name} {cadet.last_name} (CIN {cadet.cin}) — "
                f"{len(pdf_payloads)} PDF(s)..."
            )

            tmp_paths = []
            try:
                for i, pdf_bytes in enumerate(pdf_payloads):
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        suffix=".pdf",
                        prefix=f"assessment_group_{cadet_id}_{i}_",
                    )
                    os.close(tmp_fd)
                    with open(tmp_path, "wb") as f:
                        f.write(pdf_bytes)
                    tmp_paths.append(tmp_path)

                award_date = None
                raw_date = (sheets_with_pdf[0].fields or {}).get("date")
                if raw_date:
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
                        try:
                            award_date = datetime.strptime(raw_date, fmt).strftime("%d/%m/%Y")
                            break
                        except (ValueError, TypeError):
                            continue
                    if award_date is None:
                        award_date = raw_date

                add_qualification_with_attachment(
                    page=page,
                    cadet_cin=cadet.cin,
                    qualification_name=qual_name,
                    qualification_id=qual_id,
                    attachment_label=attachment_label,
                    pdf_paths=tmp_paths,
                    award_date=award_date,
                    scraper_messages=scraper_messages,
                    scraper_lock=scraper_lock,
                )

            except Exception as qual_err:
                log(f"Failed to upload '{qual_name}' for CIN {cadet.cin}: {qual_err}", "error")
                raise

            finally:
                safe_qual_name = qual_name.replace(" ", "_")
                for j, p in enumerate(tmp_paths):
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
            _uploaded_now = datetime.utcnow()
            for s in group_sheets:
                s.uploaded = True
                s.uploaded_at = _uploaded_now
            db_session.commit()

            log(f"'{qual_name}' uploaded for {cadet.first_name} {cadet.last_name} (CIN {cadet.cin}).")

        log("All qualifications processed.", "status")

    except Exception as e:
        db_session.rollback()
        log(f"Upload scraper error: {e}", "error")
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
