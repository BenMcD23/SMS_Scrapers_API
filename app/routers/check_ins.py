"""Checking cadets in on the night, before the Bader register is done.

A cadet checks themselves in by scanning the squadron's QR code or tapping its
NFC tag: both open the portal's /check-in/<code>, which calls
``POST /cadets/me/check-in/{code}``. A cadet without their phone is ticked in
by an NCO or staff on the SMS site. The session planner reads the result to
tick who's in.

None of this touches CadetAttendance, the scraped Bader register: a check-in
is a head count for the night, and the register stays the record of who
attended. The two tables never write to each other.

The night is the UK date. The server's clock isn't on UK time, and a check-in
at 23:30 BST must not land on tomorrow.

Registered before routers/cadets.py so ``/cadets/me/...`` isn't taken for a
CIN by ``/cadets/{cin}``.
"""

import secrets
from datetime import date as Date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db import get_current_cadet, get_db
from core.recruit_form import london_now, london_today
from core.security import get_user_role, require_staff, require_staff_or_nco
from database.models import Cadet, CadetCheckIn, CheckInSettings

router = APIRouter()

WRONG_CODE = "That isn't the squadron's check-in code. Ask an NCO to check you in."


def _settings(db: Session) -> CheckInSettings:
    """The one settings row, made with a fresh code on first use."""
    row = db.query(CheckInSettings).order_by(CheckInSettings.id).first()
    if row is None:
        row = CheckInSettings(code=_new_code(), changed_at=london_now())
        db.add(row)
        db.commit()
    return row


def _new_code() -> str:
    # URL-safe and short enough for a small QR code and a cheap NFC sticker,
    # long enough that nobody guesses it.
    return secrets.token_urlsafe(9)


def _code_out(row: CheckInSettings) -> dict:
    return {"code": row.code, "changed_at": row.changed_at.isoformat(timespec="seconds"),
            "changed_by": row.changed_by}


def _who(idinfo: dict) -> str:
    name = f"{idinfo.get('given_name', '')} {idinfo.get('family_name', '')}".strip()
    return name or idinfo.get("email", "")


def _night(night: Date | None, idinfo: dict) -> Date:
    """The night an NCO or staff member is changing. Tonight unless staff say
    otherwise: NCOs only ever do the door, and nobody checks anyone in to a
    night that hasn't happened."""
    today = london_today()
    night = night or today
    if night > today:
        raise HTTPException(status_code=400, detail="That night hasn't happened yet")
    if night != today and get_user_role(idinfo["email"]) != "staff":
        raise HTTPException(status_code=403, detail="Only staff can change another night's check-ins")
    return night


def _row_out(c: Cadet, check_in: CadetCheckIn | None) -> dict:
    return {
        "cin": c.cin, "first_name": c.first_name, "last_name": c.last_name,
        "rank": c.rank, "flight": c.flight,
        "checked_in": check_in is not None,
        "checked_in_at": check_in.checked_in_at.isoformat(timespec="seconds") if check_in else None,
        # None for a cadet who used the code themselves.
        "checked_in_by": check_in.checked_in_by if check_in else None,
    }


def checked_in_cins(db: Session, night: Date) -> set[int]:
    """Who has checked in on ``night``. The session planner's tick list."""
    return {cin for (cin,) in db.query(CadetCheckIn.cadet_id).filter(CadetCheckIn.date == night)}


def _record(db: Session, cin: int, night: Date, by: str | None) -> tuple[CadetCheckIn, bool]:
    """Check ``cin`` in on ``night``; (the row, whether it was already there).
    Two taps at once both land here: the unique constraint keeps one row and
    the loser reads it back rather than erroring."""
    existing = db.query(CadetCheckIn).filter_by(cadet_id=cin, date=night).first()
    if existing:
        return existing, True
    row = CadetCheckIn(cadet_id=cin, date=night, checked_in_at=london_now(), checked_in_by=by)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(CadetCheckIn).filter_by(cadet_id=cin, date=night).one(), True
    return row, False


# ── the cadet, on the portal ──────────────────────────────────────────────────

@router.post("/cadets/me/check-in/{code}")
def cadet_check_in(code: str, db: Session = Depends(get_db), cadet: Cadet = Depends(get_current_cadet)):
    """The signed-in cadet scanned the QR code or tapped the NFC tag. Checks
    them in for tonight; doing it twice is harmless and says so. The cadet
    comes from the token, never the request, so nobody can check a friend in."""
    if not secrets.compare_digest(code.encode(), _settings(db).code.encode()):
        raise HTTPException(status_code=404, detail=WRONG_CODE)
    row, already = _record(db, cadet.cin, london_today(), None)
    return {"date": row.date.isoformat(), "checked_in_at": row.checked_in_at.isoformat(timespec="seconds"),
            "already": already, "first_name": cadet.first_name}


# ── NCOs and staff, on the SMS site ───────────────────────────────────────────

@router.get("/check-ins")
def night_check_ins(
    date: Date | None = Query(None, description="The night; tonight (UK) if left out"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Everyone on the roster and whether they've checked in that night, for
    the door list. Names and ranks only: nothing personal."""
    night = date or london_today()
    rows = {r.cadet_id: r for r in db.query(CadetCheckIn).filter(CadetCheckIn.date == night)}
    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    return {
        "date": night.isoformat(),
        "today": night == london_today(),
        "checked_in": len(rows),
        "cadets": [_row_out(c, rows.get(c.cin)) for c in cadets],
    }


class CheckInBody(BaseModel):
    cin: int
    date: Date | None = None


@router.post("/check-ins")
def check_someone_in(body: CheckInBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    """An NCO ticking in a cadet who hasn't got their phone. Ticking someone
    already in changes nothing and says so."""
    night = _night(body.date, idinfo)
    cadet = db.get(Cadet, body.cin)
    if cadet is None:
        raise HTTPException(status_code=404, detail="That cadet isn't on the roster")
    row, already = _record(db, cadet.cin, night, _who(idinfo))
    return {**_row_out(cadet, row), "already": already}


@router.delete("/check-ins/{cin}", status_code=204)
def undo_check_in(
    cin: int,
    date: Date | None = Query(None),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Take a check-in off: the wrong cadet was ticked, or someone checked in
    from home. The Bader register is untouched either way."""
    night = _night(date, idinfo)
    row = db.query(CadetCheckIn).filter_by(cadet_id=cin, date=night).first()
    if row is None:
        raise HTTPException(status_code=404, detail="They aren't checked in that night")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


# ── the code itself (staff) ───────────────────────────────────────────────────

@router.get("/check-ins/code")
def check_in_code(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The code the QR code and NFC tag carry. The site turns it into the
    portal link."""
    return _code_out(_settings(db))


@router.post("/check-ins/code")
def new_check_in_code(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """A new code, for when the old one has been shared about. Every printed
    QR code and NFC tag stops working until it's replaced."""
    row = _settings(db)
    row.code = _new_code()
    row.changed_at = london_now()
    row.changed_by = idinfo.get("email")
    db.commit()
    return _code_out(row)
