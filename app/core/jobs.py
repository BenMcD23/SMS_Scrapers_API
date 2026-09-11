"""Background jobs and the schedule they run on.

Registered into the shared APScheduler by ``register_jobs`` at startup when
``SCHEDULER_ENABLED`` is true. Exactly one process may run these — two
schedulers would send the parade-night text twice and race the cleanups — so
the API runs as a single container.
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func

from core.config import DB_BACKUP_ENABLED, QUALI_EXPIRY_ALERT_EMAIL
from core.emailer import quali_expiry_email_html, send_email
from core.qualifications import quali_expiry_cutoff
from database.database import SessionLocal
from database.models import AssessmentSheet, Cadet, CadetQualification, StoresOrder
from routers import scrapers
from scripts.db_backup import run_db_backup
from texts.sender import scheduled_send_job

logger = logging.getLogger(__name__)

LONDON = "Europe/London"
COMPLETED_RETENTION = timedelta(days=182)


def cleanup_old_completed_orders() -> None:
    """Drop completed stores orders once they are six months old."""
    cutoff = datetime.now() - COMPLETED_RETENTION
    db = SessionLocal()
    try:
        orders = (
            db.query(StoresOrder)
            .filter(StoresOrder.completed == True, StoresOrder.created_at < cutoff)  # noqa: E712
            .all()
        )
        for order in orders:
            db.delete(order)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("completed-order cleanup failed")
    finally:
        db.close()


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
        # Only stamp as notified after the send is attempted, so a qualification
        # is never marked without an email having gone out for it.
        for q in quals:
            q.expiry_alert_sent_at = now
        db.commit()
    finally:
        db.close()


def register_jobs(scheduler: BaseScheduler) -> None:
    scheduler.add_job(cleanup_old_completed_orders, "interval", hours=24)
    scheduler.add_job(cleanup_old_completed_assessments, "interval", hours=24)
    scheduler.add_job(scrapers.cleanup_old_run_logs, "interval", hours=24)
    scheduler.add_job(quali_expiry_alert, CronTrigger(day_of_week="fri", hour=7, minute=0, timezone=LONDON))
    # 4pm Tue/Thu — sends the ready parade-night text for the next day (Wed/Fri).
    scheduler.add_job(scheduled_send_job, CronTrigger(day_of_week="tue,thu", hour=16, minute=0, timezone=LONDON))
    # Scraper schedules live in the database and are edited through the API,
    # which re-registers them on every edit; this loads them at startup.
    scrapers.register_schedule_jobs()
    # Daily DB backup to Google Drive — prod only (gated by the env flag).
    if DB_BACKUP_ENABLED:
        scheduler.add_job(run_db_backup, CronTrigger(hour=3, minute=0, timezone=LONDON), id="db_backup")
