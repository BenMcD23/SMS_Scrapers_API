"""Leaving process — cadets who have stopped turning up to parade.

A cadet whose last parade is four weeks or more behind the current parade night
is flagged. Staff send them one email (from the noreply account, with a
Reply-To that actually reaches someone), which starts a two-week clock; when it
runs out the row reads "start leaving process" and staff take it from there.
"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Cadet, CadetAttendance, CadetLeavingProcess

from core.attendance import attendance_state, ABSENT
from core.config import LEAVING_PROCESS_REPLY_TO
from core.db import get_db
from core.emailer import leaving_process_email_html, send_email
from core.leaving import gap_days, is_lapsed, leaving_status
from core.security import require_staff

router = APIRouter()

PARADE_NIGHT = "Parade Night"


# Enough to catch a typo before we hand an address to Gmail. Deliberately not
# pydantic's EmailStr — that pulls in email-validator for two fields, and Gmail
# rejects anything genuinely malformed anyway.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SendLeavingEmail(BaseModel):
    to: str
    replyTo: str

    @field_validator("to", "replyTo")
    @classmethod
    def _looks_like_an_email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL_RE.match(value):
            raise ValueError("must be an email address")
        return value


def _current_parade_night(db: Session) -> datetime | None:
    """Most recent parade night anyone was registered on — the reference point
    the four-week gap is measured back from."""
    return (
        db.query(func.max(CadetAttendance.date))
        .filter(CadetAttendance.register_type == PARADE_NIGHT)
        .scalar()
    )


def _last_attended_by_cadet(db: Session) -> dict[int, datetime]:
    """cadet_id -> last parade they were counted at. An authorised absence
    counts: someone excused for exams hasn't drifted away, so it breaks the gap
    the same way turning up does.
    """
    rows = (
        db.query(CadetAttendance.cadet_id, CadetAttendance.date, CadetAttendance.status)
        .filter(CadetAttendance.register_type == PARADE_NIGHT)
        .all()
    )
    last: dict[int, datetime] = {}
    for cadet_id, date, status in rows:
        if attendance_state(status) == ABSENT:
            continue
        if cadet_id not in last or date > last[cadet_id]:
            last[cadet_id] = date
    return last


@router.get("/leaving-process")
def list_leaving_process(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Every cadet who is lapsed, plus anyone with an open process already."""
    now = datetime.now()
    current_parade = _current_parade_night(db)
    last_attended = _last_attended_by_cadet(db)
    open_processes = {p.cadet_id: p for p in db.query(CadetLeavingProcess).all()}

    cadets = []
    for cadet in db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all():
        process = open_processes.get(cadet.cin)
        last = last_attended.get(cadet.cin)
        lapsed = is_lapsed(last, current_parade)

        # Keep anyone with an open process on the list even once they're back,
        # so staff can see the return and cancel it.
        if not lapsed and not process:
            continue

        status, days_left = leaving_status(process.sent_at if process else None, now)
        gap = gap_days(last, current_parade)
        cadets.append({
            "cin": cadet.cin,
            "name": f"{cadet.first_name} {cadet.last_name}".strip(),
            "rank": cadet.rank,
            "flight": cadet.flight,
            "email": cadet.email,
            "lastAttended": last.isoformat() if last else None,
            "gapDays": gap,
            "weeksAbsent": gap // 7 if gap is not None else None,
            "status": status,
            "daysLeft": days_left,
            "returned": bool(process) and not lapsed,
            "sentAt": process.sent_at.isoformat() if process else None,
            "sentTo": process.sent_to if process else None,
            "replyTo": process.reply_to if process else None,
            "sentBy": process.sent_by if process else None,
        })

    return {
        "currentParadeNight": current_parade.isoformat() if current_parade else None,
        "defaultReplyTo": LEAVING_PROCESS_REPLY_TO,
        "cadets": cadets,
    }


@router.post("/leaving-process/{cin}/send")
def send_leaving_email(
    cin: int,
    body: SendLeavingEmail,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Send the "are you still with us?" email and start the two-week clock."""
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    existing = db.query(CadetLeavingProcess).filter(CadetLeavingProcess.cadet_id == cin).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A leaving-process email has already been sent to this cadet. "
                   "Cancel it first if you need to send another.",
        )

    current_parade = _current_parade_night(db)
    last = _last_attended_by_cadet(db).get(cin)
    gap = gap_days(last, current_parade)
    weeks = (gap // 7) if gap is not None else 4

    name = (cadet.first_name or "").strip() or f"CIN {cadet.cin}"
    send_email(
        to=str(body.to),
        subject="317 Squadron — are you still with us?",
        html_body=leaving_process_email_html(name, weeks, str(body.replyTo)),
        reply_to=str(body.replyTo),
    )

    # Recorded after the send so the countdown starts from the real send time.
    # send_email swallows its own failures, so this row means "we tried", not
    # "it was delivered" — a bounce is visible in the noreply mailbox, not here.
    process = CadetLeavingProcess(
        cadet_id=cin,
        sent_at=datetime.now(),
        sent_to=str(body.to),
        reply_to=str(body.replyTo),
        sent_by=idinfo.get("email"),
    )
    db.add(process)
    db.commit()
    return {"ok": True, "sentAt": process.sent_at.isoformat()}


@router.delete("/leaving-process/{cin}", status_code=204)
def cancel_leaving_process(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Cadet came back (or it was sent in error) — drop the process entirely."""
    deleted = (
        db.query(CadetLeavingProcess)
        .filter(CadetLeavingProcess.cadet_id == cin)
        .delete(synchronize_session=False)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="No leaving process open for this cadet.")
    db.commit()
