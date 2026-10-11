"""Leaving process — cadets who have stopped turning up to parade (OC only).

A cadet whose last parade is eight weeks or more behind the current parade
night is flagged. The site does not email parents: it hands the OC their
addresses to BCC from their own account, and the OC then records that the query
went out, which starts a two-week notice clock. When it runs out the OC can have
the site email the adjutant to remove the cadet.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.attendance import ABSENT, attendance_state
from core.config import ADJUTANT_EMAIL
from core.db import get_db
from core.emailer import leaver_removal_request_email_html, send_email
from core.leaving import START_LEAVING, gap_days, is_lapsed, leaving_status, parse_emails
from core.security import require_oc
from database.models import Cadet, CadetAttendance, CadetLeavingProcess

router = APIRouter()

PARADE_NIGHT = "Parade Night"


def parse_emails_leniently(raw: str | None) -> list[str]:
    """Stored parent addresses as a list. Stored values were validated on save,
    but a bad one (a hand-edited row) must not 500 the whole list, so it's just
    dropped from the result."""
    try:
        return parse_emails(raw)
    except ValueError:
        return [p for p in (raw or "").split(";") if "@" in p]


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
    idinfo: dict = Depends(require_oc),
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
        # so the OC can see the return and cancel it.
        if not lapsed and not process:
            continue

        status, days_left = leaving_status(process.sent_at if process else None, now)
        gap = gap_days(last, current_parade)
        parents = parse_emails_leniently(cadet.parent_emails)
        cadets.append({
            "cin": cadet.cin,
            "name": f"{cadet.first_name} {cadet.last_name}".strip(),
            "rank": cadet.rank,
            "flight": cadet.flight,
            "parentEmails": parents,
            "lastAttended": last.isoformat() if last else None,
            "gapDays": gap,
            "weeksAbsent": gap // 7 if gap is not None else None,
            "status": status,
            "daysLeft": days_left,
            "returned": bool(process) and not lapsed,
            "notifiedAt": process.sent_at.isoformat() if process else None,
            "notifiedBy": process.sent_by if process else None,
            "adjRequestedAt": process.adj_requested_at.isoformat() if process and process.adj_requested_at else None,
        })

    return {
        "currentParadeNight": current_parade.isoformat() if current_parade else None,
        "adjutantConfigured": bool(ADJUTANT_EMAIL),
        "cadets": cadets,
    }


@router.post("/leaving-process/{cin}/notified")
def mark_parents_notified(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """The OC has sent the leaver query to the parents — start the two-week clock.

    The site can't see the OC's mailbox, so this is the OC's word that it went.
    """
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    if db.query(CadetLeavingProcess).filter(CadetLeavingProcess.cadet_id == cin).first():
        raise HTTPException(
            status_code=409,
            detail="The parents of this cadet have already been marked as notified. "
                   "Cancel the process first if you need to start again.",
        )

    process = CadetLeavingProcess(
        cadet_id=cin,
        sent_at=datetime.now(),
        sent_to=";".join(parse_emails_leniently(cadet.parent_emails)),
        sent_by=idinfo.get("email"),
    )
    db.add(process)
    db.commit()
    return {"ok": True, "notifiedAt": process.sent_at.isoformat()}


@router.post("/leaving-process/{cin}/request-adj")
def request_adjutant_removal(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """Email the adjutant to remove the cadet. Only once the two weeks' notice has
    run out, and only once per process."""
    process = db.query(CadetLeavingProcess).filter(CadetLeavingProcess.cadet_id == cin).first()
    if not process:
        raise HTTPException(status_code=404, detail="No leaving process open for this cadet.")
    if process.adj_requested_at:
        raise HTTPException(status_code=409, detail="The adjutant has already been asked to remove this cadet.")

    now = datetime.now()
    status, days_left = leaving_status(process.sent_at, now)
    if status != START_LEAVING:
        raise HTTPException(
            status_code=409,
            detail=f"The parents' two weeks' notice isn't up yet — {days_left} day(s) left.",
        )

    # Checked after the state rules so a misconfigured deploy doesn't mask them,
    # but before sending: send_email would skip silently and we'd record a
    # request nobody received.
    if not ADJUTANT_EMAIL:
        raise HTTPException(status_code=503, detail="No adjutant email is configured (ADJUTANT_EMAIL).")

    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    # A returned cadet shouldn't be removed: the register says they're back.
    last = _last_attended_by_cadet(db).get(cin)
    current_parade = _current_parade_night(db)
    if not is_lapsed(last, current_parade):
        raise HTTPException(
            status_code=409,
            detail="This cadet has been back on parade — cancel the process instead.",
        )
    gap = gap_days(last, current_parade)
    requester = idinfo.get("email") or ""
    name = f"{cadet.first_name} {cadet.last_name}".strip() or f"CIN {cadet.cin}"

    send_email(
        to=ADJUTANT_EMAIL,
        subject=f"317 Squadron — please remove {name} (CIN {cadet.cin})",
        html_body=leaver_removal_request_email_html(
            name, cadet.cin,
            last.strftime("%d %b %Y") if last else None,
            gap // 7 if gap is not None else None,
            process.sent_at.strftime("%d %b %Y"),
            requester,
        ),
        reply_to=requester or None,
    )

    # Recorded after the send. send_email swallows its own failures, so this
    # means "we tried", not "it was delivered".
    process.adj_requested_at = now
    process.adj_requested_by = requester
    process.adj_sent_to = ADJUTANT_EMAIL
    db.commit()
    return {"ok": True, "adjRequestedAt": now.isoformat()}


@router.delete("/leaving-process/{cin}", status_code=204)
def cancel_leaving_process(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """Cadet came back (or it was marked in error) — drop the process entirely."""
    deleted = (
        db.query(CadetLeavingProcess)
        .filter(CadetLeavingProcess.cadet_id == cin)
        .delete(synchronize_session=False)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="No leaving process open for this cadet.")
    db.commit()
