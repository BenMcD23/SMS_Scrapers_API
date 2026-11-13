"""Background jobs and the schedule they run on.

Registered into the shared APScheduler by ``register_jobs`` at startup when
``SCHEDULER_ENABLED`` is true. Exactly one process may run these — two
schedulers would send the parade-night text twice and race the cleanups — so
they only run in the process holding the leader lock (core/leader.py).
"""

import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func

from core import programme, push
from core import staff_compliance as sc
from core.config import AUDIT_RETENTION_DAYS, DB_BACKUP_ENABLED, OC_EMAIL, QUALI_EXPIRY_ALERT_EMAIL
from core.emailer import quali_expiry_email_html, send_email, staff_compliance_email_html
from core.qualifications import quali_expiry_cutoff
from database.database import SessionLocal
from database.models import (
    AssessmentSheet,
    AuditEvent,
    Cadet,
    CadetQualification,
    StaffComplianceAlert,
    StatsSnapshot,
)
from routers import assistant, scrapers, usage
from routers.staff_compliance import compliance_rows
from routers.stats import save_snapshot
from scripts.db_backup import run_db_backup
from texts.sender import scheduled_send_job

logger = logging.getLogger(__name__)

LONDON = "Europe/London"
COMPLETED_RETENTION = timedelta(days=182)


def cleanup_old_completed_assessments() -> None:
    """Drop uploaded assessment sheets once they are six months old."""
    cutoff = datetime.now() - COMPLETED_RETENTION
    db = SessionLocal()
    try:
        sheets = (
            db.query(AssessmentSheet)
            .filter(
                AssessmentSheet.uploaded == True,  # noqa: E712
                func.coalesce(AssessmentSheet.uploaded_at, AssessmentSheet.created_at) < cutoff,
            )
            .all()
        )
        for sheet in sheets:
            db.delete(sheet)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("assessment-sheet cleanup failed")
    finally:
        db.close()


def cleanup_old_audit_events() -> None:
    """Drop audit rows past the retention window.

    Kept far longer than the 182-day assessment cleanup: this is a safeguarding
    trail, so it should outlive the records it describes rather than expire
    alongside them.
    """
    cutoff = datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)
    db = SessionLocal()
    try:
        db.query(AuditEvent).filter(AuditEvent.occurred_at < cutoff).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("audit-event cleanup failed")
    finally:
        db.close()


def quali_expiry_alert() -> None:
    """Weekly (Friday) email of cadet qualifications now within 3 months of expiry.

    Each cadet+qualification is emailed exactly once: the first time it falls in
    the window it's stamped with ``expiry_alert_sent_at`` and skipped thereafter.
    """
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    db = SessionLocal()
    try:
        quals = (
            db.query(CadetQualification)
            .join(Cadet)
            .filter(
                CadetQualification.expiry_alert_sent_at.is_(None),
                CadetQualification.date_expires >= today,
                CadetQualification.date_expires <= quali_expiry_cutoff(today),
            )
            .order_by(CadetQualification.date_expires)
            .all()
        )
        if not quals:
            return
        rows = [
            (
                f"{q.cadet.first_name} {q.cadet.last_name}",
                q.qual_type,
                q.date_expires.strftime("%d/%m/%Y"),
                (q.date_expires - now).days,
            )
            for q in quals
        ]
        send_email(
            QUALI_EXPIRY_ALERT_EMAIL,
            f"Qualifications expiring in 3 months ({len(rows)})",
            quali_expiry_email_html(rows),
        )
        push.notify(
            "qualifications",
            f"{len(rows)} qualification{'' if len(rows) == 1 else 's'} expiring",
            ", ".join(f"{name} ({qual})" for name, qual, _, _ in rows[:3]) + (" and more" if len(rows) > 3 else ""),
            "/stats",
        )
        # Only stamp as notified after the send is attempted, so a qualification
        # is never marked without an email having gone out for it.
        for q in quals:
            q.expiry_alert_sent_at = now
        db.commit()
    finally:
        db.close()


def staff_compliance_alert() -> None:
    """Weekly email to each member of staff whose DBS, BPSS or mandatory
    training is due within 60 days, lapsed or not done, and a digest to the OC.

    Each item is mentioned once per stage (due soon, then lapsed) for a given
    expiry date, so a renewal that brings a new date starts afresh. "Not done"
    has no date and is never stamped: it stays on the OC's digest each week
    until it's done, but the person is only emailed when something new is due.
    """
    today = date.today()
    now = datetime.now()
    db = SessionLocal()
    try:
        sent = {(a.staff_id, a.item, a.expires_on, a.stage) for a in db.query(StaffComplianceAlert)}
        digest, stamps = [], []
        for person in compliance_rows(db, today):
            chase = sc.due(person["items"])
            if not chase:
                continue
            for i in chase:
                digest.append((person["name"], i["name"], i["status"],
                               i["expires"].strftime("%d/%m/%Y") if i["expires"] else ""))
            new = [i for i in chase if i["expires"] and i["status"] in (sc.EXPIRING, sc.EXPIRED)
                   and (person["cin"], i["key"], i["expires"], i["status"]) not in sent]
            if not new:
                continue
            # Someone with no email on the roster is still stamped: the OC's
            # digest below is how they hear about it.
            if person["email"]:
                send_email(
                    person["email"],
                    "Your DBS or mandatory training is due",
                    staff_compliance_email_html(person["name"].split(" ")[0], [
                        (person["name"], i["name"], i["status"],
                         i["expires"].strftime("%d/%m/%Y") if i["expires"] else "") for i in chase
                    ]),
                )
                push.notify("compliance", "DBS or training due",
                            ", ".join(i["name"] for i in new[:3]) + (" and more" if len(new) > 3 else ""),
                            "/staff/compliance", emails=[person["email"]])
            stamps += [StaffComplianceAlert(staff_id=person["cin"], item=i["key"], expires_on=i["expires"],
                                            stage=i["status"], sent_at=now) for i in new]
        # The OC hears about everything outstanding, only in weeks something new
        # was sent; otherwise "not done" items would email them every Friday.
        if stamps:
            for oc in [e.strip() for e in OC_EMAIL.split(",") if e.strip()]:
                send_email(oc, f"Staff DBS and training due ({len(digest)})", staff_compliance_email_html(None, digest))
            db.add_all(stamps)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("staff compliance alert failed")
    finally:
        db.close()


def weekly_stats_snapshot() -> None:
    """Make sure every week has a stats snapshot, scraper run or not.

    Snapshots are otherwise only taken when someone runs the cadet-quali
    scraper, so a quiet week left a gap in every trend. Skips the week if one
    was already taken since Monday — a second copy of the same numbers adds
    nothing, and the trends keep one point per week anyway.
    """
    now = datetime.now()
    monday = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
    db = SessionLocal()
    try:
        if db.query(StatsSnapshot).filter(StatsSnapshot.captured_at >= monday).first():
            return
        save_snapshot(db)
    except Exception:
        db.rollback()
        logger.exception("weekly stats snapshot failed")
    finally:
        db.close()


def import_recent_programme() -> None:
    """Re-import last month, this month and next from the programme doc.

    The doc is written ahead and edited as plans change, so a weekly read
    keeps the record matching it; last month is included to catch edits made
    after the fact (a cancelled night, a swapped session)."""
    today = date.today()
    first = date(today.year, today.month, 1)
    last_month = (first - timedelta(days=1)).replace(day=1)
    next_month = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    db = SessionLocal()
    try:
        programme.import_months(db, programme.months_between(last_month, next_month))
    except Exception:
        db.rollback()
        logger.exception("weekly programme import failed")
    finally:
        db.close()


def register_jobs(scheduler: BaseScheduler) -> None:
    scheduler.add_job(cleanup_old_completed_assessments, "interval", hours=24)
    scheduler.add_job(scrapers.cleanup_old_run_logs, "interval", hours=24)
    scheduler.add_job(usage.cleanup_old_usage, "interval", hours=24)
    scheduler.add_job(cleanup_old_audit_events, "interval", hours=24)
    scheduler.add_job(assistant.cleanup_old_chats, "interval", hours=24)
    scheduler.add_job(quali_expiry_alert, CronTrigger(day_of_week="fri", hour=7, minute=0, timezone=LONDON))
    scheduler.add_job(staff_compliance_alert, CronTrigger(day_of_week="fri", hour=7, minute=10, timezone=LONDON))
    # Sunday evening: after any weekend scrape, before the week's trend closes.
    scheduler.add_job(weekly_stats_snapshot, CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=LONDON))
    # Sunday evening, before the week's parade nights.
    scheduler.add_job(import_recent_programme, CronTrigger(day_of_week="sun", hour=20, minute=30, timezone=LONDON))
    # 4pm Tue/Thu — sends the ready parade-night text for the next day (Wed/Fri).
    scheduler.add_job(scheduled_send_job, CronTrigger(day_of_week="tue,thu", hour=16, minute=0, timezone=LONDON))
    # Scraper schedules live in the database and are edited through the API,
    # which re-registers them on every edit; this loads them at startup.
    scrapers.register_schedule_jobs()
    # Daily DB backup to Google Drive — prod only (gated by the env flag).
    if DB_BACKUP_ENABLED:
        scheduler.add_job(run_db_backup, CronTrigger(hour=3, minute=0, timezone=LONDON), id="db_backup")
