"""Scheduled maintenance jobs, and the registry that names them.

These used to be APScheduler jobs registered in the app lifespan. On Lambda
there is no long-lived process to hold a scheduler, so each one is now an
EventBridge Scheduler rule that invokes the function with `{"job": "<name>"}`
and lambda_handler.py dispatches it through JOBS below.

Outside Lambda (local dev, and the home stack during the cutover) api.py still
runs them on an in-process scheduler, so behaviour there is unchanged.

The scraper *schedules* are deliberately not here — they live on the worker, so
a cloud outage doesn't stop a scheduled scrape.
"""

from datetime import datetime, timedelta

from sqlalchemy import func

from database.database import SessionLocal
from database.models import AssessmentSheet, Cadet, CadetQualification, StoresOrder

from core.config import DB_BACKUP_ENABLED, QUALI_EXPIRY_ALERT_EMAIL
from core.emailer import send_email, quali_expiry_email_html
from core.qualifications import quali_expiry_cutoff
from routers.scrapers import cleanup_old_run_logs
from scripts.db_backup import run_db_backup
from texts.sender import scheduled_send_job


def cleanup_old_completed_orders():
    cutoff = datetime.now() - timedelta(days=182)
    db = SessionLocal()
    try:
        orders = (
            db.query(StoresOrder)
            .filter(StoresOrder.completed == True, StoresOrder.created_at < cutoff)
            .all()
        )
        for order in orders:
            db.delete(order)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def cleanup_old_completed_assessments():
    cutoff = datetime.now() - timedelta(days=182)
    db = SessionLocal()
    try:
        sheets = (
            db.query(AssessmentSheet)
            .filter(
                AssessmentSheet.uploaded == True,
                func.coalesce(AssessmentSheet.uploaded_at, AssessmentSheet.created_at) < cutoff,
            )
            .all()
        )
        for sheet in sheets:
            db.delete(sheet)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def quali_expiry_alert():
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
        # Only stamp as notified after the send is attempted, so a qualification
        # is never marked without an email having gone out for it.
        for q in quals:
            q.expiry_alert_sent_at = now
        db.commit()
    finally:
        db.close()


def db_backup():
    """Daily dump to Google Drive — prod only, gated by the env flag.

    Neon's free tier has no downloadable backups, so this is the *only*
    recovery path. The gate stays a runtime check rather than a missing
    EventBridge rule so a misconfigured environment fails loudly in the logs.
    """
    if not DB_BACKUP_ENABLED:
        print("[db_backup] DB_BACKUP_ENABLED is not set — skipping", flush=True)
        return
    run_db_backup()


def keep_warm():
    """No-op target for the 5-minute EventBridge ping.

    Verifying a Google id_token on a cold container costs a round trip to
    Google's certs endpoint, on top of the container start itself. Keeping one
    warm is free and takes that off the first real request of the day.
    """
    return


# Names are the contract with the EventBridge rules — changing one means
# changing the rule's payload too.
JOBS = {
    "cleanup_orders":      cleanup_old_completed_orders,
    "cleanup_assessments": cleanup_old_completed_assessments,
    "cleanup_run_logs":    cleanup_old_run_logs,
    "quali_expiry":        quali_expiry_alert,
    "parade_texts":        scheduled_send_job,
    "db_backup":           db_backup,
    "keep_warm":           keep_warm,
}
