"""The parade-night attendance report: build it, email it, and the 21:30 job.

On Wednesday and Friday evenings the job scrapes Bader (the cadet-quali
scraper is what reads each cadet's attendance register), then freezes that
night's cadet register into a ParadeReport and emails it to the recipients
staff chose. A night with no parade register gets no report and no email: not
every Wednesday or Friday is a parade.

Kept out of the router so the job, the "build now" endpoint and the tests all
go through one build-and-send path.
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from core.attendance import ABSENT, AUTHORISED, PRESENT, attendance_state
from core.config import SITE_BASE_URL
from core.emailer import parade_report_email_html, send_email
from core.night_outlook import PARADE_NIGHT
from core.security import get_user_role
from database.database import SessionLocal
from database.models import (
    Cadet,
    CadetAttendance,
    ParadeReport,
    ParadeReportEntry,
    ParadeReportRecipient,
    ParadeReportSettings,
)

logger = logging.getLogger(__name__)

# Fixed rather than configurable: parade finishes and the register is marked
# by then. Europe/London, so it stays 21:30 across the clock change.
RUN_DAYS = "wed,fri"
RUN_HOUR, RUN_MINUTE = 21, 30

# Who may be sent the report or be put on the list: SMS users of NCO rank and up.
REPORT_ROLES = ("staff", "snco", "nco")

STATE_ORDER = {ABSENT: 0, AUTHORISED: 1, PRESENT: 2}


def night_label(night: date) -> str:
    """"Wednesday 26 November 2026", for the email subject and heading."""
    return f"{night:%A} {night.day} {night:%B %Y}"


def report_link(night: date) -> str:
    return f"{SITE_BASE_URL}/attendance/reports/{night.isoformat()}"


def get_settings(db: Session) -> ParadeReportSettings | None:
    return db.query(ParadeReportSettings).order_by(ParadeReportSettings.id).first()


def build_report(db: Session, night: date, generated_by: str) -> ParadeReport | None:
    """Freeze one night's cadet parade register into a report, replacing any
    earlier report for that night. None if there was no register that night.

    Cadets only: staff are on a separate register (Staff_Attendance) that this
    never reads. A register with no status on anyone is a register nobody has
    marked yet, which is treated as no register rather than reported as the
    whole squadron absent.
    """
    start = datetime(night.year, night.month, night.day)
    rows = (
        db.query(CadetAttendance, Cadet)
        .join(Cadet, CadetAttendance.cadet_id == Cadet.cin)
        .filter(
            CadetAttendance.register_type == PARADE_NIGHT,
            CadetAttendance.date >= start,
            CadetAttendance.date < start + timedelta(days=1),
        )
        .all()
    )
    if not any((record.status or "").strip() for record, _ in rows):
        return None

    existing = db.query(ParadeReport).filter(ParadeReport.parade_date == night).first()
    if existing:
        db.delete(existing)
        db.flush()

    # One mark per cadet: a duplicate register row (a cadet listed twice) must
    # not count them twice.
    seen: dict[int, ParadeReportEntry] = {}
    for record, cadet in rows:
        state = attendance_state(record.status)
        if cadet.cin in seen and STATE_ORDER[seen[cadet.cin].state] >= STATE_ORDER[state]:
            continue
        seen[cadet.cin] = ParadeReportEntry(
            cadet_cin=cadet.cin,
            name=f"{cadet.first_name or ''} {cadet.last_name or ''}".strip(),
            rank=cadet.rank,
            flight=cadet.flight,
            status=record.status,
            state=state,
        )
    entries = sorted(seen.values(), key=lambda e: (STATE_ORDER[e.state], e.name.casefold()))
    report = ParadeReport(parade_date=night, generated_at=datetime.now(), generated_by=generated_by,
                          entries=entries)
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def entry_json(entry: ParadeReportEntry) -> dict:
    return {"cin": entry.cadet_cin, "name": entry.name, "rank": entry.rank, "flight": entry.flight,
            "status": entry.status, "state": entry.state}


def recipients_who_can_receive(db: Session) -> list[str]:
    """The list's addresses that still belong to an NCO, SNCO or staff member.

    Checked at send time as well as when added: someone taken out of the
    groups since must stop getting cadets' names by email.
    """
    emails = [r.email for r in db.query(ParadeReportRecipient).order_by(ParadeReportRecipient.email)]
    allowed = [e for e in emails if get_user_role(e) in REPORT_ROLES]
    for skipped in sorted(set(emails) - set(allowed)):
        logger.warning(f"parade report: {skipped} is no longer staff or an NCO, not emailed")
    return allowed


def email_report(db: Session, report: ParadeReport) -> list[str]:
    """Send the report to everyone on the list, and record who it went to."""
    to = recipients_who_can_receive(db)
    if not to:
        logger.warning(f"parade report {report.parade_date}: no one to email")
        return []
    label = night_label(report.parade_date)
    body = parade_report_email_html(label, report_link(report.parade_date),
                                    [entry_json(e) for e in report.entries])
    sent = []
    for address in to:
        # One failure mustn't stop the rest of the list getting it.
        try:
            send_email(address, f"Parade night attendance: {label}", body)
            sent.append(address)
        except Exception:
            logger.exception(f"parade report {report.parade_date}: email to {address} failed")
    if sent:
        report.emailed_at = datetime.now()
        report.emailed_to = sent
        db.commit()
    return sent


def run_parade_report(today: date | None = None) -> None:
    """The 21:30 Wednesday/Friday job: scrape, then build and email the report.

    Does nothing while the report is switched off. A failed scrape gets no
    report: half-updated registers would send people the wrong absentees, and
    staff can build it by hand from the page once the scraper has been re-run.
    """
    # Imported here: routers.scrapers pulls in the scraper stack, which the
    # report endpoints and tests don't need.
    from routers.scrapers import run_scraper_and_wait

    night = today or date.today()
    db = SessionLocal()
    try:
        settings = get_settings(db)
        if not settings or not settings.enabled:
            return
        user = settings.user
        if not user or not user.bader_credentials:
            logger.warning("parade report: whoever switched it on has no Bader credentials saved, skipping")
            return
        user_id, email = user.id, user.email
    finally:
        db.close()

    if not run_scraper_and_wait("cadet-quali", user_id, email, f"parade report ({email})"):
        logger.warning(f"parade report {night}: the cadet scrape didn't finish cleanly, no report")
        return

    db = SessionLocal()
    try:
        report = build_report(db, night, "schedule")
        if report is None:
            logger.info(f"parade report {night}: no parade register tonight, nothing sent")
            return
        email_report(db, report)
    except Exception:
        db.rollback()
        logger.exception(f"parade report {night} failed")
    finally:
        db.close()
