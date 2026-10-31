"""DofE activity logs: Skills, Volunteering and Physical, logged week by week.

Replaces the per-cadet tabs of the "Activity Log" spreadsheet. Staff switch a
cadet's log on (the portal hides the page until they do). The cadet, or staff
on their behalf, sets each section up (activity, goal, assessor, how long,
start date) and changes it whenever they change their mind, then adds what
they did and for how long, several weeks at once when catching up.

When a section reaches its weeks, staff are emailed the PDF the spreadsheet
used to be printed for. The rules for weeks and finishing are in
core.activity_log.

Cadet routes live under /cadets/me and only ever touch the signed-in cadet's
own log; the staff routes take a CIN and are staff only.
"""

import io
import logging
import unicodedata
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from core.activity_log import SECTIONS, WEEKLY_MINUTES, minutes_by_week, progress, week_start
from core.config import ACTIVITY_LOG_ALERT_EMAIL
from core.db import get_current_cadet, get_db
from core.emailer import activity_log_complete_email_html, send_email
from core.http import content_disposition
from core.security import require_staff
from database.models import ActivityLog, ActivityLogEnrolment, ActivityLogEntry, Cadet, Staff
from form_generators.activity_log_pdf import build_activity_log_pdf

logger = logging.getLogger(__name__)
router = APIRouter()

Section = Literal["skills", "volunteering", "physical"]
MAX_ENTRIES_PER_SAVE = 60
MAX_ENTRY_MINUTES = 600


def _today() -> date:
    """Patched in tests, so "this week" doesn't depend on when they run."""
    return date.today()


def _required(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Fill this in")
    return value


class SetupBody(BaseModel):
    activity: str = Field(max_length=300)
    goal: str = Field(max_length=1000)
    # Optional: a cadet setting up often doesn't know yet, and shouldn't be
    # stuck at the first screen because of it. The PDF box is left blank.
    assessor_name: str = Field(default="", max_length=120)
    assessor_role: str = Field(default="", max_length=120)
    duration_months: Literal[3, 6, 12, 18]
    start_date: date

    _activity = field_validator("activity", "goal")(_required)

    @field_validator("assessor_name", "assessor_role")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()


class EntryIn(BaseModel):
    date: date
    description: str = Field(max_length=1000)
    minutes: int = Field(ge=1, le=MAX_ENTRY_MINUTES)

    _description = field_validator("description")(_required)


class SignOffBody(BaseModel):
    initials: str

    @field_validator("initials")
    @classmethod
    def _letters(cls, v: str) -> str:
        v = "".join(v.split()).replace(".", "").upper()
        if not (1 <= len(v) <= 4 and v.isalpha()):
            raise ValueError("Initials are 1 to 4 letters")
        return v


class EntriesBody(BaseModel):
    entries: list[EntryIn] = Field(min_length=1, max_length=MAX_ENTRIES_PER_SAVE)


class EnrolmentBody(BaseModel):
    """Only what's sent is changed: a level change from the cadet's page
    mustn't wipe the DofE number or assessor already on file."""
    level: Literal["Bronze", "Silver", "Gold"] = "Bronze"
    dofe_number: str | None = Field(default=None, max_length=40)
    assessor_name: str | None = Field(default=None, max_length=120)
    # A staff member's CIN, or null for someone outside SMS.
    assessor_cin: int | None = None

    @field_validator("dofe_number", "assessor_name")
    @classmethod
    def _strip_optional(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


# ── helpers ──────────────────────────────────────────────────────────────────

def _cadet_name(cadet: Cadet) -> str:
    name = f"{cadet.first_name or ''} {cadet.last_name or ''}".strip()
    return f"{cadet.rank} {name}".strip() if cadet.rank else name


def _fold(name: str | None) -> str:
    """Sort key that files Ó Briain under O, not after Z."""
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().casefold()


def _uk(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def _entry_dict(e: ActivityLogEntry) -> dict:
    return {"id": e.id, "date": e.date.isoformat(), "description": e.description, "minutes": e.minutes,
            "created_at": e.created_at.isoformat(), "signed_off_initials": e.signed_off_initials,
            "signed_off_at": e.signed_off_at.isoformat() if e.signed_off_at else None}


def _log_dict(log: ActivityLog, today: date) -> dict:
    return {
        "id": log.id,
        "activity": log.activity,
        "goal": log.goal,
        "assessor_name": log.assessor_name,
        "assessor_role": log.assessor_role,
        "duration_months": log.duration_months,
        "start_date": log.start_date.isoformat(),
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        "entries": [_entry_dict(e) for e in sorted(log.entries, key=lambda e: (e.date, e.id), reverse=True)],
        **progress(log, today),
    }


def _logs_for(db: Session, cadet: Cadet, level: str) -> dict[str, ActivityLog]:
    logs = (db.query(ActivityLog)
            .filter(ActivityLog.cadet_id == cadet.cin, ActivityLog.level == level)
            .options(selectinload(ActivityLog.entries)).all())
    return {log.section: log for log in logs}


def _report_ready(logs: dict[str, ActivityLog]) -> bool:
    """Every section is set up and has run its full length, which is when the
    assessor's final report can be written."""
    return all(key in logs and logs[key].completed_at is not None for key in SECTIONS)


def _staff_name(db: Session, email: str | None) -> str | None:
    """Who ticked the report, as a name where they're on the staff roster."""
    if not email:
        return None
    staff = db.query(Staff).filter(func.lower(Staff.email) == email.lower()).first()
    return f"{staff.first_name} {staff.last_name}" if staff else email


def _overview(db: Session, cadet: Cadet) -> dict:
    enrolment = cadet.activity_log_enrolment
    if not enrolment:
        return {"enabled": False, "cin": cadet.cin, "name": _cadet_name(cadet), "sections": []}
    today = _today()
    logs = _logs_for(db, cadet, enrolment.level)
    return {
        "enabled": True,
        "cin": cadet.cin,
        "name": _cadet_name(cadet),
        "level": enrolment.level,
        "dofe_number": enrolment.dofe_number,
        "assessor_name": enrolment.assessor_name,
        "assessor_cin": enrolment.assessor_staff_cin,
        "report_ready": _report_ready(logs),
        "report_done_at": enrolment.report_done_at.isoformat() if enrolment.report_done_at else None,
        "report_done_by": _staff_name(db, enrolment.report_done_by),
        "sections": [
            {"section": key, "label": label,
             "log": _log_dict(logs[key], today) if key in logs else None}
            for key, label in SECTIONS.items()
        ],
    }


def _enrolment(cadet: Cadet, *, staff: bool) -> ActivityLogEnrolment:
    if not cadet.activity_log_enrolment:
        raise HTTPException(
            status_code=409 if staff else 403,
            detail="Turn on this cadet's DofE activity log first." if staff
            else "Your DofE activity log isn't switched on yet. Speak to a member of staff.",
        )
    return cadet.activity_log_enrolment


def _log_or_404(db: Session, cadet: Cadet, section: str, *, staff: bool) -> ActivityLog:
    level = _enrolment(cadet, staff=staff).level
    log = (db.query(ActivityLog)
           .filter(ActivityLog.cadet_id == cadet.cin, ActivityLog.section == section, ActivityLog.level == level)
           .first())
    if not log:
        raise HTTPException(status_code=404, detail=f"Set up your {SECTIONS[section]} section first.")
    return log


def _check_entry_date(log: ActivityLog, d: date) -> None:
    if d > _today():
        raise HTTPException(status_code=400, detail=f"{_uk(d)} hasn't happened yet. Pick today or an earlier day.")
    if d < log.start_date:
        raise HTTPException(status_code=400,
                            detail=f"{_uk(d)} is before you started on {_uk(log.start_date)}.")


def _pdf_fields(log: ActivityLog, cadet: Cadet) -> dict:
    prog = progress(log, _today())
    return {
        "cadet_name": _cadet_name(cadet),
        "section": SECTIONS[log.section],
        "level": log.level,
        "duration_months": log.duration_months,
        "activity": log.activity,
        "goal": log.goal,
        "assessor_name": log.assessor_name,
        "assessor_role": log.assessor_role,
        "start_date": log.start_date,
        "end_date": date.fromisoformat(prog["end_date"]),
        "weeks_done": prog["weeks_done"],
        "weeks_needed": prog["weeks_needed"],
        "entries": [{"date": e.date, "description": e.description, "minutes": e.minutes,
                     "initials": e.signed_off_initials or ""}
                    for e in sorted(log.entries, key=lambda e: (e.date, e.id))],
    }


def _pdf_filename(log: ActivityLog, cadet: Cadet) -> str:
    return f"{SECTIONS[log.section]} Section {log.level} DofE Activity Log - {cadet.first_name} {cadet.last_name}.pdf"


def _settle(db: Session, log: ActivityLog, cadet: Cadet) -> None:
    """Commit a change, then email staff the PDF if it just finished the
    section. An edit that takes it back under clears the flag, so finishing
    again sends a fresh copy rather than leaving staff with a stale one.

    The email goes after the commit: a failed send never loses the cadet's
    entry, and send_email logs rather than raises."""
    db.flush()
    db.refresh(log)
    log.updated_at = datetime.now()
    complete = progress(log, _today())["complete"]
    just_finished = complete and log.completed_at is None
    if just_finished:
        log.completed_at = datetime.now()
    elif not complete:
        log.completed_at = None
    db.commit()
    if just_finished:
        fields = _pdf_fields(log, cadet)
        send_email(
            ACTIVITY_LOG_ALERT_EMAIL,
            f"{fields['cadet_name']} has finished their {log.level} DofE {fields['section']} section",
            activity_log_complete_email_html(
                fields["cadet_name"], fields["section"], log.level, fields["weeks_done"],
                fields["assessor_name"], log.activity,
            ),
            attachment=build_activity_log_pdf(fields),
            attachment_filename=_pdf_filename(log, cadet),
        )


def _setup(db: Session, cadet: Cadet, section: str, body: SetupBody, *, staff: bool) -> dict:
    level = _enrolment(cadet, staff=staff).level
    today = _today()
    if body.start_date > today + timedelta(days=366) or body.start_date < today - timedelta(days=3 * 366):
        raise HTTPException(status_code=400, detail="Pick a start date within the last three years.")
    log = (db.query(ActivityLog)
           .filter(ActivityLog.cadet_id == cadet.cin, ActivityLog.section == section, ActivityLog.level == level)
           .first())
    now = datetime.now()
    if log is None:
        log = ActivityLog(cadet_id=cadet.cin, section=section, level=level, created_at=now)
        db.add(log)
    elif log.entries and min(e.date for e in log.entries) < body.start_date:
        earliest = min(e.date for e in log.entries)
        raise HTTPException(
            status_code=409,
            detail=f"You've logged {_uk(earliest)}, which is before that date. Change or remove it first.",
        )
    log.activity, log.goal = body.activity, body.goal
    log.assessor_name, log.assessor_role = body.assessor_name, body.assessor_role
    log.duration_months, log.start_date = body.duration_months, body.start_date
    log.updated_at = now
    _settle(db, log, cadet)
    return _overview(db, cadet)


def _add_entries(db: Session, cadet: Cadet, section: str, body: EntriesBody, who: str, *, staff: bool) -> dict:
    log = _log_or_404(db, cadet, section, staff=staff)
    # All or nothing: one bad date in a catch-up shouldn't save half of it.
    for e in body.entries:
        _check_entry_date(log, e.date)
    now = datetime.now()
    for e in body.entries:
        db.add(ActivityLogEntry(log_id=log.id, date=e.date, description=e.description, minutes=e.minutes,
                                created_at=now, created_by=who))
    _settle(db, log, cadet)
    return _overview(db, cadet)


def _entry_or_404(db: Session, log: ActivityLog, entry_id: int) -> ActivityLogEntry:
    entry = db.query(ActivityLogEntry).filter(ActivityLogEntry.id == entry_id,
                                              ActivityLogEntry.log_id == log.id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="That entry isn't in this log.")
    return entry


def _edit_entry(db: Session, cadet: Cadet, section: str, entry_id: int, body: EntryIn, *, staff: bool) -> dict:
    log = _log_or_404(db, cadet, section, staff=staff)
    entry = _entry_or_404(db, log, entry_id)
    _check_entry_date(log, body.date)
    if (entry.date, entry.description, entry.minutes) != (body.date, body.description, body.minutes):
        # The assessor signed off what it said before, not this.
        entry.signed_off_initials = entry.signed_off_by = entry.signed_off_at = None
    entry.date, entry.description, entry.minutes = body.date, body.description, body.minutes
    _settle(db, log, cadet)
    return _overview(db, cadet)


def _delete_entry(db: Session, cadet: Cadet, section: str, entry_id: int, *, staff: bool) -> dict:
    log = _log_or_404(db, cadet, section, staff=staff)
    db.delete(_entry_or_404(db, log, entry_id))
    _settle(db, log, cadet)
    return _overview(db, cadet)


def _cadet_or_404(db: Session, cin: int) -> Cadet:
    cadet = db.get(Cadet, cin)
    if not cadet:
        raise HTTPException(status_code=404, detail="Cadet not found")
    return cadet


# ── Cadet (portal) ───────────────────────────────────────────────────────────

@router.get("/cadets/me/activity-log")
def my_activity_log(db: Session = Depends(get_db), cadet: Cadet = Depends(get_current_cadet)):
    """``enabled: false`` (not a 403) when staff haven't switched it on: the
    portal asks on every page to decide whether to show the link at all."""
    return _overview(db, cadet)


@router.put("/cadets/me/activity-log/{section}")
def my_setup(section: Section, body: SetupBody, db: Session = Depends(get_db),
             cadet: Cadet = Depends(get_current_cadet)):
    return _setup(db, cadet, section, body, staff=False)


@router.post("/cadets/me/activity-log/{section}/entries", status_code=201)
def my_add_entries(section: Section, body: EntriesBody, db: Session = Depends(get_db),
                   cadet: Cadet = Depends(get_current_cadet)):
    return _add_entries(db, cadet, section, body, cadet.email, staff=False)


@router.patch("/cadets/me/activity-log/{section}/entries/{entry_id}")
def my_edit_entry(section: Section, entry_id: int, body: EntryIn, db: Session = Depends(get_db),
                  cadet: Cadet = Depends(get_current_cadet)):
    return _edit_entry(db, cadet, section, entry_id, body, staff=False)


@router.delete("/cadets/me/activity-log/{section}/entries/{entry_id}")
def my_delete_entry(section: Section, entry_id: int, db: Session = Depends(get_db),
                    cadet: Cadet = Depends(get_current_cadet)):
    return _delete_entry(db, cadet, section, entry_id, staff=False)


# ── Staff ────────────────────────────────────────────────────────────────────

GRID_WEEKS = 8
MAX_GRID_WEEKS = 26


def _cell(log: ActivityLog | None, minutes: int, week: date, current: date) -> str:
    """One section in one week of the grid. ``off`` is a week with nothing
    due: not set up, before the start, after finishing, or still to come.
    The current week is ``due`` rather than ``missed`` while there are days
    left to do it."""
    if log is None or week > current or week < week_start(log.start_date):
        return "off"
    if log.completed_at is not None and week > week_start(log.completed_at.date()):
        return "off"
    if minutes >= WEEKLY_MINUTES:
        return "done"
    if minutes:
        return "short"
    return "due" if week == current else "missed"


@router.get("/activity-logs/grid")
def weekly_grid(
    end: date | None = Query(None, description="Any day in the last week shown; this week when omitted"),
    weeks: int = Query(GRID_WEEKS, ge=1, le=MAX_GRID_WEEKS),
    db: Session = Depends(get_db),
    _: dict = Depends(require_staff),
):
    """Every cadet with their log switched on, against a run of Monday–Sunday
    weeks: which sections got their hour each week. One look shows who's
    logging, who's slipping and who never does.

    ``behind`` is about this week, whatever weeks are shown: a section that's
    due and still under an hour, or nothing set up at all (a section not set
    up yet isn't chased on its own; cadets often start them one at a time)."""
    today = _today()
    current = week_start(today)
    last = min(week_start(end or today), current)
    shown = [last - timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]
    enrolments = (db.query(ActivityLogEnrolment)
                  .options(selectinload(ActivityLogEnrolment.cadet)).all())
    logs = (db.query(ActivityLog).options(selectinload(ActivityLog.entries))
            .filter(ActivityLog.cadet_id.in_([e.cadet_id for e in enrolments])).all())
    by_cadet: dict[tuple[int, str], dict[str, ActivityLog]] = {}
    for log in logs:
        by_cadet.setdefault((log.cadet_id, log.level), {})[log.section] = log

    rows = []
    for enrolment in enrolments:
        cadet = enrolment.cadet
        mine = by_cadet.get((cadet.cin, enrolment.level), {})
        totals = {key: minutes_by_week(log.entries) for key, log in mine.items()}

        def cell(key: str, week: date, mine=mine, totals=totals) -> dict:
            minutes = totals.get(key, {}).get(week, 0)
            return {"state": _cell(mine.get(key), minutes, week, current), "minutes": minutes}

        sections = []
        for key, label in SECTIONS.items():
            log = mine.get(key)
            prog = progress(log, today) if log else None
            sections.append({
                "section": key,
                "label": label,
                "set_up": log is not None,
                "complete": log is not None and log.completed_at is not None,
                "weeks_done": prog["weeks_done"] if prog else 0,
                "weeks_needed": prog["weeks_needed"] if prog else None,
                "last_added": max((e.created_at for e in log.entries), default=None) if log else None,
            })
        now = [cell(key, current)["state"] for key in SECTIONS]
        rows.append({
            "_sort": (_fold(cadet.last_name), _fold(cadet.first_name)),
            "cin": cadet.cin,
            "name": f"{cadet.first_name} {cadet.last_name}",
            "rank": cadet.rank,
            "flight": cadet.flight,
            "level": enrolment.level,
            "assessor_name": enrolment.assessor_name,
            "assessor_cin": enrolment.assessor_staff_cin,
            "report_done": enrolment.report_done_at is not None,
            "report_due": _report_ready(mine) and enrolment.report_done_at is None,
            "behind": any(state in ("due", "short") for state in now) or not mine,
            "sections": sections,
            "weeks": [{"week_start": w.isoformat(), "cells": {key: cell(key, w) for key in SECTIONS}}
                      for w in shown],
        })
    rows.sort(key=lambda r: r.pop("_sort"))
    return {"weeks": [w.isoformat() for w in shown], "current_week": current.isoformat(),
            "cadets": rows}


@router.get("/activity-logs/cadets/{cin}")
def cadet_activity_log(cin: int, db: Session = Depends(get_db), _: dict = Depends(require_staff)):
    return _overview(db, _cadet_or_404(db, cin))


@router.put("/activity-logs/cadets/{cin}/enrolment")
def enrol(cin: int, body: EnrolmentBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Switch a cadet's log on (or change their level). Their portal shows the
    Activity Log page from their next visit.

    The DofE number and assessor are only changed when sent. Moving to a new
    level clears a report ticked off at the old one: it covered other sections."""
    cadet = _cadet_or_404(db, cin)
    sent = body.model_fields_set
    if "assessor_cin" in sent and body.assessor_cin is not None:
        staff = db.get(Staff, body.assessor_cin)
        if not staff:
            raise HTTPException(status_code=400, detail="That assessor isn't on the staff list.")
        # The name is the staff member's own, so it can't drift from the roster.
        body.assessor_name = f"{staff.first_name} {staff.last_name}"
    enrolment = cadet.activity_log_enrolment
    if enrolment:
        if enrolment.level != body.level:
            enrolment.report_done_at = enrolment.report_done_by = None
        enrolment.level = body.level
    else:
        enrolment = ActivityLogEnrolment(cadet_id=cin, level=body.level, enabled_at=datetime.now(),
                                         enabled_by=idinfo.get("email"))
        db.add(enrolment)
    if "dofe_number" in sent and body.dofe_number:
        enrolment.dofe_number = body.dofe_number
    if "assessor_name" in sent and body.assessor_name:
        enrolment.assessor_name = body.assessor_name
        enrolment.assessor_staff_cin = body.assessor_cin
    db.commit()
    db.refresh(cadet)
    return _overview(db, cadet)


@router.delete("/activity-logs/cadets/{cin}/enrolment")
def unenrol(cin: int, db: Session = Depends(get_db), _: dict = Depends(require_staff)):
    """Hide the page from the cadet. Their logs are kept, so switching it back
    on picks up where they were."""
    cadet = _cadet_or_404(db, cin)
    if not cadet.activity_log_enrolment:
        raise HTTPException(status_code=404, detail="This cadet's DofE activity log isn't switched on.")
    db.delete(cadet.activity_log_enrolment)
    db.commit()
    db.refresh(cadet)
    return _overview(db, cadet)


# Registered before the {section} routes below, which would otherwise try to
# read "report" as a section name and answer 422.
@router.put("/activity-logs/cadets/{cin}/report")
def mark_report_done(cin: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The assessor ticking off the final report. Refused until every section
    has run its full length, so it can't be ticked early by mistake."""
    cadet = _cadet_or_404(db, cin)
    enrolment = _enrolment(cadet, staff=True)
    if not _report_ready(_logs_for(db, cadet, enrolment.level)):
        raise HTTPException(status_code=409,
                            detail="Every section has to run its full length before the report is done.")
    enrolment.report_done_at, enrolment.report_done_by = datetime.now(), idinfo.get("email")
    db.commit()
    db.refresh(cadet)
    return _overview(db, cadet)


@router.delete("/activity-logs/cadets/{cin}/report")
def undo_report_done(cin: int, db: Session = Depends(get_db), _: dict = Depends(require_staff)):
    cadet = _cadet_or_404(db, cin)
    enrolment = _enrolment(cadet, staff=True)
    enrolment.report_done_at = enrolment.report_done_by = None
    db.commit()
    db.refresh(cadet)
    return _overview(db, cadet)


@router.put("/activity-logs/cadets/{cin}/{section}")
def staff_setup(cin: int, section: Section, body: SetupBody, db: Session = Depends(get_db),
                _: dict = Depends(require_staff)):
    return _setup(db, _cadet_or_404(db, cin), section, body, staff=True)


@router.post("/activity-logs/cadets/{cin}/{section}/entries", status_code=201)
def staff_add_entries(cin: int, section: Section, body: EntriesBody, db: Session = Depends(get_db),
                      idinfo: dict = Depends(require_staff)):
    return _add_entries(db, _cadet_or_404(db, cin), section, body, idinfo.get("email"), staff=True)


@router.patch("/activity-logs/cadets/{cin}/{section}/entries/{entry_id}")
def staff_edit_entry(cin: int, section: Section, entry_id: int, body: EntryIn, db: Session = Depends(get_db),
                     _: dict = Depends(require_staff)):
    return _edit_entry(db, _cadet_or_404(db, cin), section, entry_id, body, staff=True)


@router.delete("/activity-logs/cadets/{cin}/{section}/entries/{entry_id}")
def staff_delete_entry(cin: int, section: Section, entry_id: int, db: Session = Depends(get_db),
                       _: dict = Depends(require_staff)):
    return _delete_entry(db, _cadet_or_404(db, cin), section, entry_id, staff=True)


@router.put("/activity-logs/cadets/{cin}/{section}/entries/{entry_id}/sign-off")
def staff_sign_off_entry(cin: int, section: Section, entry_id: int, body: SignOffBody,
                         db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The assessor's initials against one entry, as on the paper sheet. Signing
    again replaces them."""
    cadet = _cadet_or_404(db, cin)
    entry = _entry_or_404(db, _log_or_404(db, cadet, section, staff=True), entry_id)
    entry.signed_off_initials, entry.signed_off_by, entry.signed_off_at = (
        body.initials, idinfo.get("email"), datetime.now())
    db.commit()
    return _overview(db, cadet)


@router.delete("/activity-logs/cadets/{cin}/{section}/entries/{entry_id}/sign-off")
def staff_undo_sign_off(cin: int, section: Section, entry_id: int, db: Session = Depends(get_db),
                        _: dict = Depends(require_staff)):
    cadet = _cadet_or_404(db, cin)
    entry = _entry_or_404(db, _log_or_404(db, cadet, section, staff=True), entry_id)
    entry.signed_off_initials = entry.signed_off_by = entry.signed_off_at = None
    db.commit()
    return _overview(db, cadet)


@router.get("/activity-logs/cadets/{cin}/{section}/pdf")
def staff_pdf(cin: int, section: Section, db: Session = Depends(get_db), _: dict = Depends(require_staff)):
    """The log as the paper sheet, at any point — not only once it's finished."""
    cadet = _cadet_or_404(db, cin)
    log = _log_or_404(db, cadet, section, staff=True)
    pdf = build_activity_log_pdf(_pdf_fields(log, cadet))
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": content_disposition("attachment", _pdf_filename(log, cadet))},
    )
