"""The programme builder: each month's parade-night programme, built from a
bank of sessions instead of written in a Google Doc.

Staff build it: nights, sessions dropped into a half for one or more flights,
rooms, and the staff, NCOs or cadets on each. The API checks it as it goes
(people or rooms in two places, someone on holiday, a room too small, a
flight with nothing on, lessons out of order) and suggests what to put in the
gaps (core/programme_builder.py).

NCOs and SNCOs read it and comment, Google Docs style: a thread on the
programme, a night or a session; @mentions and assigned action items email the
person with the thread and a link straight to it; replies and resolving email
everyone in the thread. Anyone who can comment can resolve or reopen a thread;
only the author edits a comment, and staff can delete any.

Publishing puts the PDF on the website (routers/programme.py) and writes the
month into the programme record that stats, training and check-in read, so a
built month is indistinguishable downstream from an imported one.
"""

import io
import logging
import re
from collections import Counter
from datetime import date, datetime
from datetime import date as Date  # NightBody has a field called date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func
from sqlalchemy.orm import Session

import database.database as database
from core import programme as programme_record
from core import programme_ai, push
from core import programme_builder as pb
from core.actor import actor_name
from core.config import SITE_BASE_URL
from core.db import get_db, get_or_create_user
from core.emailer import programme_comment_email_html, programme_comment_subject, send_email
from core.http import content_disposition
from core.security import get_user_role, require_staff, require_staff_or_nco
from database.models import (
    Event317,
    ProgrammeBankItem,
    ProgrammeChange,
    ProgrammeComment,
    ProgrammeDraft,
    ProgrammeDraftNight,
    ProgrammeKind,
    ProgrammeNight,
    ProgrammeRoom,
    ProgrammeSession,
    ProgrammeSlot,
    User,
)
from form_generators.programme_pdf import build_programme_pdf, when_label
from routers import programme as website

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/programme-builder", tags=["programme builder"])

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
MAX_NIGHTS = 10
MAX_SLOTS_PER_NIGHT = 30
MAX_PEOPLE = 40
HISTORY_SHOWN = 50


# ── bodies ──────────────────────────────────────────────────────────────────

class CreateProgramme(BaseModel):
    month: str = Field(pattern=MONTH)
    # "empty": the month's parade nights with nothing on them; "record": what
    # the programme record already has for the month (an imported doc);
    # "copy": another month's sessions (built or an old doc), each night on the
    # new night nearest its date (core.programme_builder.align_nights).
    start: Literal["empty", "record", "copy"] = "empty"
    copy_from: str | None = Field(None, pattern=MONTH)


class KeyDate(BaseModel):
    when: str = Field(min_length=1, max_length=40)
    what: str = Field(min_length=1, max_length=120)


class ProgrammeBody(BaseModel):
    # Only the fields sent change.
    notes: str | None = Field(None, max_length=2000)
    key_dates: list[KeyDate] | None = Field(None, max_length=15)


class NightBody(BaseModel):
    date: Date | None = None
    uniform: str | None = Field(None, max_length=120)
    duty_nco: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=1000)


class SlotBody(BaseModel):
    night_id: int | None = None
    period: int | None = None
    flights: list[str] | None = None
    title: str | None = Field(None, max_length=120)
    category: str | None = None
    bank_item_id: int | None = None
    room_id: int | None = None
    instructor: str | None = Field(None, max_length=200)
    people: list[str] | None = Field(None, max_length=MAX_PEOPLE)
    subject_key: str | None = Field(None, max_length=80)
    part: int | None = Field(None, ge=1, le=20)
    notes: str | None = Field(None, max_length=1000)


class CopySlot(BaseModel):
    night_ids: list[int] = Field(min_length=1, max_length=MAX_NIGHTS)


class Pick(BaseModel):
    """One suggestion to accept as it was offered: a bank session, or joining a session already there."""
    night_id: int
    period: int
    flight: str
    bank_item_id: int | None = None
    slot_id: int | None = None


class FillBody(BaseModel):
    # Each {night_id, period, flight}; none fills every gap.
    cells: list[dict] | None = None
    # Or exactly these, as offered (the AI draft's picks): cells are then ignored.
    picks: list[Pick] | None = Field(None, max_length=200)


class BankBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: str
    whole_night: bool = False
    room_id: int | None = None
    instructor: str | None = Field(None, max_length=200)
    people: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)
    subject_key: str | None = Field(None, max_length=80)
    part: int | None = Field(None, ge=1, le=20)
    notes: str | None = Field(None, max_length=1000)
    active: bool = True


class BankImport(BaseModel):
    titles: list[str] = Field(min_length=1, max_length=100)


class KindBody(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    colour: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    hidden: bool = False


class RoomBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    capacity: int | None = Field(None, ge=1, le=500)
    notes: str | None = Field(None, max_length=500)
    active: bool = True


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    night_id: int | None = None
    slot_id: int | None = None
    parent_id: int | None = None
    # Assign the thread to the first person @'d, as Docs' "Assign to" box.
    assign: bool = False


class EditComment(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class PublishBody(BaseModel):
    website: bool = True
    record: bool = True
    notify: bool = False


# ── helpers ─────────────────────────────────────────────────────────────────

def _is_staff(idinfo: dict) -> bool:
    return get_user_role(idinfo.get("email", "")) == "staff"


def _draft(db: Session, month: str) -> ProgrammeDraft:
    draft = db.query(ProgrammeDraft).filter(ProgrammeDraft.month == month).first()
    if not draft:
        raise HTTPException(status_code=404, detail="There's no programme for that month yet")
    return draft


def _night(db: Session, night_id: int) -> ProgrammeDraftNight:
    night = db.get(ProgrammeDraftNight, night_id)
    if not night:
        raise HTTPException(status_code=404, detail="Night not found")
    return night


def _slot(db: Session, slot_id: int) -> ProgrammeSlot:
    slot = db.get(ProgrammeSlot, slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="Session not found")
    return slot


def _touch(draft: ProgrammeDraft, idinfo: dict, summary: str) -> None:
    """Every edit stamps the month and leaves a line in its history."""
    now = datetime.now()
    draft.updated_at = now
    draft.updated_by = actor_name(idinfo)
    draft.changes.append(ProgrammeChange(at=now, by=actor_name(idinfo), summary=summary[:300]))


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _programme_name(month: str) -> str:
    first, _ = pb.month_bounds(month)
    return f"{first:%B %Y} programme"


def _status(draft: ProgrammeDraft) -> str:
    if not draft.published_at:
        return "draft"
    return "changed" if draft.updated_at > draft.published_at else "published"


def _where(slot: ProgrammeSlot, flights: list[str]) -> str:
    return (f"{pb.who_label(slot.flights or [], flights)}, {pb.period_label(slot.period).lower()}, "
            f"{pb.night_label(slot.night.date)}")


def _user_name(user: User | None) -> str:
    if not user:
        return "Someone"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email


def _author(user: User, people: dict[str, pb.Person], is_staff: bool) -> dict:
    """A comment's author as the roster names them, else as their account
    does. Addresses are for staff only: NCOs get names."""
    person = pb.person_for_email(people, user.email)
    return {"name": person.name if person else _user_name(user), "key": person.key if person else None,
            "email": user.email if is_staff else None}


def _ref(key: str | None, people: dict[str, pb.Person]) -> dict | None:
    if not key:
        return None
    person = people.get(key)
    return {"key": key, "name": person.name if person else "Someone no longer on the roster"}


def _validate_slot(db: Session, draft: ProgrammeDraft, body: SlotBody, ctx: pb.Context) -> None:
    if body.period is not None and body.period not in pb.PERIODS:
        raise HTTPException(status_code=422, detail="A session is in the 1st half, the 2nd half or all night")
    if body.flights is not None:
        unknown = [f for f in body.flights if f not in ctx.flights]
        if unknown:
            raise HTTPException(status_code=422, detail=f"There's no {unknown[0]} Flight")
    if body.category is not None and body.category not in programme_record.kind_labels(db):
        raise HTTPException(status_code=422, detail="Pick a kind of session from the list")
    if body.title is not None and not body.title.strip():
        raise HTTPException(status_code=422, detail="Give the session a name")
    if body.room_id is not None:
        room = db.get(ProgrammeRoom, body.room_id)
        if not room:
            raise HTTPException(status_code=422, detail="That room isn't in the list")
    if body.people:
        missing = [k for k in body.people if k not in ctx.people]
        if missing:
            raise HTTPException(status_code=422, detail="Someone on the session isn't on the roster")
    if body.night_id is not None:
        night = db.get(ProgrammeDraftNight, body.night_id)
        if not night or night.programme_id != draft.id:
            raise HTTPException(status_code=422, detail="That night isn't in this programme")


# ── serialising ─────────────────────────────────────────────────────────────

def _slot_json(slot: ProgrammeSlot, ctx: pb.Context, comments: dict[int, int]) -> dict:
    return {
        "id": slot.id, "night_id": slot.night_id, "period": slot.period, "flights": slot.flights or [],
        "title": slot.title, "category": slot.category, "bank_item_id": slot.bank_item_id,
        "room_id": slot.room_id, "instructor": slot.instructor,
        "people": [{"key": k, "name": ctx.people[k].name if k in ctx.people else "Someone no longer on the roster",
                    "kind": ctx.people[k].kind if k in ctx.people else "unknown"} for k in slot.people or []],
        "subject_key": slot.subject_key, "part": slot.part, "notes": slot.notes,
        "headcount": pb.slot_headcount(slot, ctx.expected, ctx.flights),
        "open_comments": comments.get(slot.id, 0),
    }


def _comment_json(c: ProgrammeComment, viewer: User, people: dict[str, pb.Person], is_staff: bool) -> dict:
    return {
        "id": c.id, "parent_id": c.parent_id, "night_id": c.night_id, "slot_id": c.slot_id, "quote": c.quote,
        "author": _author(c.author, people, is_staff), "body": c.body,
        "mentions": [_ref(k, people) for k in c.mentions or []],
        "assignee": _ref(c.assignee, people),
        "created_at": _iso(c.created_at), "edited_at": _iso(c.edited_at),
        "resolved_at": _iso(c.resolved_at), "resolved_by": c.resolved_by,
        "can_edit": c.author_id == viewer.id, "can_delete": c.author_id == viewer.id or is_staff,
    }


def _threads(draft: ProgrammeDraft, viewer: User, people: dict[str, pb.Person], is_staff: bool) -> list[dict]:
    roots = [c for c in draft.comments if c.parent_id is None]
    out = []
    for root in roots:
        data = _comment_json(root, viewer, people, is_staff)
        data["replies"] = [_comment_json(r, viewer, people, is_staff) for r in root.replies]
        out.append(data)
    # Open threads first, then newest activity first, as Docs lists them.
    def last(t):
        return max([t["created_at"]] + [r["created_at"] for r in t["replies"]])
    out.sort(key=lambda t: last(t), reverse=True)
    out.sort(key=lambda t: t["resolved_at"] is not None)
    return out


def _detail(db: Session, draft: ProgrammeDraft, idinfo: dict) -> dict:
    is_staff = _is_staff(idinfo)
    viewer = get_or_create_user(db, idinfo)
    ctx = pb.build_context(db, draft)
    open_by_slot: dict[int, int] = {}
    for c in draft.comments:
        if c.parent_id is None and c.slot_id and not c.resolved_at:
            open_by_slot[c.slot_id] = open_by_slot.get(c.slot_id, 0) + 1
    return {
        "month": draft.month,
        "name": _programme_name(draft.month),
        "status": _status(draft),
        "notes": draft.notes,
        "key_dates": draft.key_dates or [],
        "can_edit": is_staff,
        "updated_at": _iso(draft.updated_at), "updated_by": draft.updated_by,
        "published_at": _iso(draft.published_at), "published_by": draft.published_by,
        "flights": [{"key": f, "label": pb.flight_label(f), "expected": ctx.expected.get(f, 0)} for f in ctx.flights],
        "rooms": [_room_json(r) for r in sorted(ctx.rooms.values(), key=lambda r: (r.position, r.name))],
        "nights": [{
            "id": n.id, "date": n.date.isoformat(), "uniform": n.uniform, "duty_nco": n.duty_nco, "notes": n.notes,
            "slots": [_slot_json(s, ctx, open_by_slot) for s in n.slots],
        } for n in draft.nights],
        "issues": pb.check(draft, ctx),
        "comments": _threads(draft, viewer, ctx.people, is_staff),
        "history": [{"at": _iso(c.at), "by": c.by, "summary": c.summary}
                    for c in reversed(draft.changes[-HISTORY_SHOWN:])],
    }


def _room_json(r: ProgrammeRoom) -> dict:
    return {"id": r.id, "name": r.name, "capacity": r.capacity, "notes": r.notes, "position": r.position,
            "active": r.active}


def _bank_json(item: ProgrammeBankItem, usage: dict, people: dict[str, pb.Person]) -> dict:
    times, last = usage.get(item.title.casefold(), (0, None))
    return {"id": item.id, "title": item.title, "category": item.category, "whole_night": item.whole_night,
            "room_id": item.room_id, "instructor": item.instructor,
            "people": [_ref(k, people) for k in item.people or []], "subject_key": item.subject_key,
            "part": item.part, "notes": item.notes, "active": item.active, "times_run": times,
            "last_run": _iso(last)}


def _event_dates(db: Session, first: date, after: date) -> list[dict]:
    """The squadron's events (from Bader) in a month, as a new programme's
    important dates to start from."""
    events = (db.query(Event317)
              .filter(Event317.date_from < datetime.combine(after, datetime.min.time()),
                      Event317.date_to >= datetime.combine(first, datetime.min.time()))
              .order_by(Event317.date_from, Event317.title).limit(15))
    return [{"when": when_label(e.date_from.date(), e.date_to.date())[:40], "what": e.title.strip()[:120]}
            for e in events]


def _record_nights(db: Session, month: str) -> list[ProgrammeNight]:
    first, after = pb.month_bounds(month)
    return (db.query(ProgrammeNight).filter(ProgrammeNight.date >= first, ProgrammeNight.date < after)
            .order_by(ProgrammeNight.date).all())


# The old docs' names for rooms that have since been renamed, or misspelt.
ROOM_ALIASES = {"drill hall": "main parade hall", "hanger": "hangar", "outdoors": "outside"}


def _room_ids(db: Session) -> dict[str, int]:
    """Lower-cased room name (and old names for it) to its id, for record rooms."""
    ids = {r.name.casefold(): r.id for r in db.query(ProgrammeRoom)}
    ids.update({old: ids[new] for old, new in ROOM_ALIASES.items() if new in ids and old not in ids})
    return ids


def _from_record(n: ProgrammeNight, on: date, room_ids: dict[str, int]) -> ProgrammeDraftNight:
    """A night of the record as a builder night on ``on``: one slot per
    activity per half, with every flight that did it, in the room the record
    has when it's one of ours."""
    night = ProgrammeDraftNight(date=on, uniform=n.uniform, duty_nco=n.duty_nco)
    grouped: dict[tuple, list] = {}
    for s in sorted(n.sessions, key=lambda s: (s.period, s.id)):
        grouped.setdefault((s.period, s.activity, s.category, s.instructor), []).append(s)
    for (period, activity, category, instructor), rows in grouped.items():
        rooms = Counter(i for r in rows if (i := room_ids.get((r.room or "").strip().casefold())))
        night.slots.append(ProgrammeSlot(period=period, flights=sorted({r.flight for r in rows}), title=activity,
                                         category=category, instructor=instructor, people=[],
                                         room_id=rooms.most_common(1)[0][0] if rooms else None))
    return night


# ── months ──────────────────────────────────────────────────────────────────

@router.get("/months")
def list_months(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    """Every built month, newest first, and which months the record has that
    aren't built yet (to start one from)."""
    drafts = db.query(ProgrammeDraft).order_by(ProgrammeDraft.month.desc()).all()
    out = []
    for d in drafts:
        roots = [c for c in d.comments if c.parent_id is None]
        out.append({
            "month": d.month, "name": _programme_name(d.month), "status": _status(d),
            "nights": len(d.nights), "sessions": sum(len(n.slots) for n in d.nights),
            "open_comments": sum(1 for c in roots if not c.resolved_at),
            "updated_at": _iso(d.updated_at), "updated_by": d.updated_by, "published_at": _iso(d.published_at),
        })
    built = {d.month for d in drafts}
    # Casting a date to text gives ISO on Postgres and SQLite alike.
    month_col = func.substr(cast(ProgrammeNight.date, Text), 1, 7)
    in_record = {m for (m,) in db.query(month_col).distinct()}
    record = sorted(in_record - built, reverse=True)
    today = date.today()
    nxt = f"{today.year + today.month // 12}-{today.month % 12 + 1:02d}"
    return {"programmes": out, "record_months": record[:24],
            # Every month a new one can copy: built ones and the record's (old docs).
            "copy_months": sorted(in_record | built, reverse=True), "parade_weekdays": pb.PARADE_WEEKDAYS,
            "next_month": nxt if nxt not in built else None}


@router.post("/months", status_code=201)
def create_month(body: CreateProgramme, db: Session = Depends(get_db),
                 idinfo: dict = Depends(require_staff)) -> dict:
    """Start a month: its parade nights, empty, or from the record or another
    built month."""
    if db.query(ProgrammeDraft).filter(ProgrammeDraft.month == body.month).first():
        raise HTTPException(status_code=409, detail="That month already has a programme")
    first, after = pb.month_bounds(body.month)
    now = datetime.now()
    draft = ProgrammeDraft(month=body.month, created_at=now, created_by=actor_name(idinfo), updated_at=now,
                           updated_by=actor_name(idinfo), key_dates=_event_dates(db, first, after))
    db.add(draft)

    if body.start == "record":
        nights = _record_nights(db, body.month)
        if not nights:
            raise HTTPException(status_code=404, detail="The record has nothing for that month")
        for n in nights:
            draft.nights.append(_from_record(n, n.date, _room_ids(db)))
        summary = "Started from the programme record"
    elif body.start == "copy":
        if not body.copy_from:
            raise HTTPException(status_code=422, detail="Say which month to copy")
        # A built month if there is one, else what the record has (an old doc).
        source = db.query(ProgrammeDraft).filter(ProgrammeDraft.month == body.copy_from).first()
        old_nights = source.nights if source else _record_nights(db, body.copy_from)
        if not old_nights:
            raise HTTPException(status_code=404, detail="There's nothing to copy for that month")
        dates = pb.month_dates(body.month, pb.PARADE_WEEKDAYS)[:MAX_NIGHTS]
        onto = pb.align_nights([n.date for n in old_nights], dates)
        copied = {j: old_nights[i] for i, j in onto.items()}
        for j, d in enumerate(dates):
            old = copied.get(j)
            if old is None:
                draft.nights.append(ProgrammeDraftNight(date=d))
            elif not source:
                draft.nights.append(_from_record(old, d, _room_ids(db)))
            else:
                night = ProgrammeDraftNight(date=d, uniform=old.uniform, duty_nco=old.duty_nco)
                draft.nights.append(night)
                for s in old.slots:
                    night.slots.append(ProgrammeSlot(
                        period=s.period, flights=list(s.flights or []), title=s.title, category=s.category,
                        bank_item_id=s.bank_item_id, room_id=s.room_id, instructor=s.instructor,
                        people=list(s.people or []), subject_key=s.subject_key, part=s.part, notes=s.notes))
        summary = f"Started as a copy of {_programme_name(body.copy_from)}"
    else:
        for d in pb.month_dates(body.month, pb.PARADE_WEEKDAYS)[:MAX_NIGHTS]:
            draft.nights.append(ProgrammeDraftNight(date=d))
        summary = "Started the programme"
    _touch(draft, idinfo, summary)
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


@router.get("/months/{month}")
def get_month(month: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    return _detail(db, _draft(db, month), idinfo)


@router.patch("/months/{month}")
def edit_month(month: str, body: ProgrammeBody, db: Session = Depends(get_db),
               idinfo: dict = Depends(require_staff)) -> dict:
    draft = _draft(db, month)
    sent = body.model_fields_set
    if "notes" in sent:
        draft.notes = (body.notes or "").strip() or None
        _touch(draft, idinfo, "Changed the programme's notes")
    if "key_dates" in sent:
        draft.key_dates = [{"when": d.when.strip(), "what": d.what.strip()} for d in body.key_dates or []]
        _touch(draft, idinfo, "Changed the important dates")
    db.commit()
    return _detail(db, draft, idinfo)


@router.delete("/months/{month}")
def delete_month(month: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    """Throws away the built month and its comments. What was published stays
    on the website and in the record."""
    db.delete(_draft(db, month))
    db.commit()
    return {"ok": True}


# ── nights ──────────────────────────────────────────────────────────────────

@router.post("/months/{month}/nights", status_code=201)
def add_night(month: str, body: NightBody, db: Session = Depends(get_db),
              idinfo: dict = Depends(require_staff)) -> dict:
    draft = _draft(db, month)
    first, after = pb.month_bounds(month)
    if not body.date or not (first <= body.date < after):
        raise HTTPException(status_code=422, detail="Pick a date in this month")
    if any(n.date == body.date for n in draft.nights):
        raise HTTPException(status_code=409, detail="That night is already in the programme")
    if len(draft.nights) >= MAX_NIGHTS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_NIGHTS} nights in a month")
    draft.nights.append(ProgrammeDraftNight(date=body.date, uniform=body.uniform, duty_nco=body.duty_nco,
                                            notes=body.notes))
    _touch(draft, idinfo, f"Added {pb.night_label(body.date)}")
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


@router.patch("/nights/{night_id}")
def edit_night(night_id: int, body: NightBody, db: Session = Depends(get_db),
               idinfo: dict = Depends(require_staff)) -> dict:
    night = _night(db, night_id)
    draft = night.programme
    sent = body.model_fields_set
    changed = []
    if "date" in sent and body.date and body.date != night.date:
        first, after = pb.month_bounds(draft.month)
        if not (first <= body.date < after):
            raise HTTPException(status_code=422, detail="Pick a date in this month")
        if any(n.date == body.date for n in draft.nights if n.id != night.id):
            raise HTTPException(status_code=409, detail="That night is already in the programme")
        changed.append(f"moved to {pb.night_label(body.date)}")
        night.date = body.date
    for name, label in (("uniform", "uniform"), ("duty_nco", "duty NCO"), ("notes", "notes")):
        if name in sent:
            value = (getattr(body, name) or "").strip() or None
            if value != getattr(night, name):
                setattr(night, name, value)
                changed.append(f"{label} {'set to ' + value if value and name != 'notes' else 'changed'}")
    if changed:
        _touch(draft, idinfo, f"{pb.night_label(night.date)}: {', '.join(changed)}")
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


@router.delete("/nights/{night_id}")
def delete_night(night_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    night = _night(db, night_id)
    draft = night.programme
    _touch(draft, idinfo, f"Removed {pb.night_label(night.date)} and its {len(night.slots)} session(s)")
    draft.nights.remove(night)
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


# ── sessions ────────────────────────────────────────────────────────────────

@router.post("/months/{month}/slots", status_code=201)
def add_slot(month: str, body: SlotBody, db: Session = Depends(get_db),
             idinfo: dict = Depends(require_staff)) -> dict:
    """Put a session on a night. From the bank, only the bank item and where
    it goes are needed: the rest is copied from the bank and can be changed."""
    draft = _draft(db, month)
    ctx = pb.build_context(db, draft)
    if body.night_id is None:
        raise HTTPException(status_code=422, detail="Say which night the session is on")
    _validate_slot(db, draft, body, ctx)
    night = db.get(ProgrammeDraftNight, body.night_id)
    if len(night.slots) >= MAX_SLOTS_PER_NIGHT:
        raise HTTPException(status_code=422, detail=f"At most {MAX_SLOTS_PER_NIGHT} sessions on a night")
    item = db.get(ProgrammeBankItem, body.bank_item_id) if body.bank_item_id else None
    if body.bank_item_id and not item:
        raise HTTPException(status_code=422, detail="That session isn't in the bank any more")
    sent = body.model_fields_set
    period = body.period if "period" in sent else (None if item and item.whole_night else 1)
    title = (body.title or (item.title if item else "")).strip()
    if not title:
        raise HTTPException(status_code=422, detail="Give the session a name")
    slot = ProgrammeSlot(
        period=period, flights=body.flights or [], title=title,
        category=body.category or (item.category if item else None) or programme_record.category_for(title),
        bank_item_id=item.id if item else None,
        room_id=body.room_id if "room_id" in sent else (item.room_id if item else None),
        instructor=(body.instructor if "instructor" in sent else (item.instructor if item else None)) or None,
        people=body.people if "people" in sent else list(item.people or []) if item else [],
        subject_key=body.subject_key if "subject_key" in sent else (item.subject_key if item else None),
        part=body.part if "part" in sent else (item.part if item else None),
        notes=(body.notes or "").strip() or None,
    )
    night.slots.append(slot)
    db.flush()
    _touch(draft, idinfo, f"Added {slot.title} for {_where(slot, ctx.flights)}")
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


@router.patch("/slots/{slot_id}")
def edit_slot(slot_id: int, body: SlotBody, db: Session = Depends(get_db),
              idinfo: dict = Depends(require_staff)) -> dict:
    """Change any part of a session; moving it is changing its night, half or
    flights. Only the fields sent change."""
    slot = _slot(db, slot_id)
    draft = slot.night.programme
    ctx = pb.build_context(db, draft)
    _validate_slot(db, draft, body, ctx)
    sent = body.model_fields_set
    before = _where(slot, ctx.flights)
    if "night_id" in sent and body.night_id and body.night_id != slot.night_id:
        # Reassigned, not removed and re-added: the nights' delete-orphan
        # cascade would delete a slot taken out of its old night's list.
        slot.night = db.get(ProgrammeDraftNight, body.night_id)
    for name in ("period", "room_id", "subject_key", "part"):
        if name in sent:
            setattr(slot, name, getattr(body, name))
    for name in ("flights", "people"):
        if name in sent:
            setattr(slot, name, getattr(body, name) or [])
    for name in ("title", "category"):
        if name in sent and getattr(body, name):
            setattr(slot, name, getattr(body, name).strip())
    for name in ("instructor", "notes"):
        if name in sent:
            setattr(slot, name, (getattr(body, name) or "").strip() or None)
    after = _where(slot, ctx.flights)
    _touch(draft, idinfo, f"Moved {slot.title} from {before} to {after}" if before != after
           else f"Changed {slot.title} ({after})")
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


@router.delete("/slots/{slot_id}")
def delete_slot(slot_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    slot = _slot(db, slot_id)
    draft = slot.night.programme
    ctx = pb.build_context(db, draft)
    _touch(draft, idinfo, f"Removed {slot.title} from {_where(slot, ctx.flights)}")
    slot.night.slots.remove(slot)
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


@router.post("/slots/{slot_id}/copy")
def copy_slot(slot_id: int, body: CopySlot, db: Session = Depends(get_db),
              idinfo: dict = Depends(require_staff)) -> dict:
    """The same session on other nights: same half, flights, room and people."""
    slot = _slot(db, slot_id)
    draft = slot.night.programme
    nights = {n.id: n for n in draft.nights}
    if any(i not in nights for i in body.night_ids):
        raise HTTPException(status_code=422, detail="Those nights aren't all in this programme")
    copied = 0
    for night_id in dict.fromkeys(body.night_ids):
        if night_id == slot.night_id:
            continue
        nights[night_id].slots.append(ProgrammeSlot(
            period=slot.period, flights=list(slot.flights or []), title=slot.title, category=slot.category,
            bank_item_id=slot.bank_item_id, room_id=slot.room_id, instructor=slot.instructor,
            people=list(slot.people or []), subject_key=slot.subject_key, part=slot.part, notes=slot.notes))
        copied += 1
    if copied:
        _touch(draft, idinfo, f"Copied {slot.title} to {copied} more night{'s' if copied != 1 else ''}")
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


# ── suggestions ─────────────────────────────────────────────────────────────

def _bank(db: Session) -> list[ProgrammeBankItem]:
    return db.query(ProgrammeBankItem).filter(ProgrammeBankItem.active.is_(True)).order_by(
        ProgrammeBankItem.title).all()


@router.get("/months/{month}/suggestions")
def suggestions(month: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    draft = _draft(db, month)
    return pb.suggest(draft, pb.build_context(db, draft), _bank(db))


@router.post("/months/{month}/fill")
def fill(month: str, body: FillBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    """Fill gaps with the top suggestion, one at a time, so each choice sees
    the ones before it (nothing goes in twice, rooms aren't doubled)."""
    draft = _draft(db, month)
    ctx = pb.build_context(db, draft)
    bank = _bank(db)
    if body.picks is not None:
        added = _apply_picks(draft, ctx, {b.id: b for b in bank}, body.picks)
        if added:
            _touch(draft, idinfo, f"Added {added} suggested session{'s' if added != 1 else ''}")
        db.commit()
        db.refresh(draft)
        return {"filled": added, "programme": _detail(db, draft, idinfo)}
    if body.cells:
        try:
            cells = [(int(c["night_id"]), int(c["period"]), str(c["flight"])) for c in body.cells]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Each gap needs a night, a half and a flight")
    else:
        cells = [(n.id, p, f) for n in draft.nights for p in pb.PERIODS for f in ctx.flights]
    nights = {n.id: n for n in draft.nights}
    added = 0
    for cell in cells:
        if cell[0] not in nights:
            raise HTTPException(status_code=422, detail="That night isn't in this programme")
        found = pb.suggest(draft, ctx, bank, per_cell=1, only=cell)
        if not found:
            continue
        option = found[0]["options"][0]
        night = nights[cell[0]]
        if option["kind"] == "join":
            joining = next(s for s in night.slots if s.id == option["slot_id"])
            joining.flights = sorted(set(joining.flights or []) | {cell[2]}, key=ctx.flights.index)
        else:
            item = next(b for b in bank if b.id == option["bank_item_id"])
            night.slots.append(ProgrammeSlot(
                period=cell[1], flights=[cell[2]], title=item.title, category=item.category, bank_item_id=item.id,
                room_id=option["room_id"], instructor=item.instructor, people=list(item.people or []),
                subject_key=item.subject_key,
                part=item.part, notes=None))
        db.flush()
        added += 1
    if added:
        _touch(draft, idinfo, f"Filled {added} gap{'s' if added != 1 else ''} from the suggestions")
    db.commit()
    db.refresh(draft)
    return {"filled": added, "programme": _detail(db, draft, idinfo)}


def _apply_picks(draft: ProgrammeDraft, ctx: pb.Context, bank: dict[int, ProgrammeBankItem],
                 picks: list[Pick]) -> int:
    """Put each pick in its gap. A gap filled since it was suggested is left
    alone; the same bank session picked for two flights in one half becomes
    one session for both; each gets a room free at the time."""
    nights = {n.id: n for n in draft.nights}
    made: dict[tuple[int, int, int], ProgrammeSlot] = {}
    added = 0
    for pick in picks:
        night = nights.get(pick.night_id)
        if not night or pick.period not in pb.PERIODS or pick.flight not in ctx.flights:
            raise HTTPException(status_code=422, detail="That suggestion isn't for a gap in this programme")
        taken = any(pick.period in pb.slot_periods(s) and pick.flight in pb.slot_flights(s, ctx.flights)
                    for s in night.slots)
        if taken:
            continue
        if pick.slot_id is not None:
            slot = next((s for s in night.slots if s.id == pick.slot_id and pick.period in pb.slot_periods(s)
                         and s.flights), None)
            if not slot:
                continue
            slot.flights = sorted(set(slot.flights) | {pick.flight}, key=ctx.flights.index)
        elif (item := bank.get(pick.bank_item_id)) is not None:
            key = (night.id, pick.period, item.id)
            if key in made:
                made[key].flights = sorted(set(made[key].flights) | {pick.flight}, key=ctx.flights.index)
            else:
                busy = {s.room_id for s in night.slots if s.room_id and pick.period in pb.slot_periods(s)}
                room = pb.free_room(ctx, busy, ctx.expected.get(pick.flight, 0) + 1, item.room_id)
                made[key] = ProgrammeSlot(
                    period=pick.period, flights=[pick.flight], title=item.title, category=item.category,
                    bank_item_id=item.id, room_id=room.id if room else None, instructor=item.instructor,
                    people=list(item.people or []), subject_key=item.subject_key, part=item.part)
                night.slots.append(made[key])
        else:
            continue
        added += 1
    return added


@router.post("/months/{month}/ai-draft")
def ai_draft(month: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    """The AI's pick for each gap, from the rules' options, with the past
    programmes in mind. Changes nothing: staff accept the picks (POST /fill
    with ``picks``)."""
    draft = _draft(db, month)
    return programme_ai.draft(db, draft, pb.build_context(db, draft), _bank(db))


# ── PDF and publishing ──────────────────────────────────────────────────────

def _pdf(db: Session, draft: ProgrammeDraft) -> bytes:
    ctx = pb.build_context(db, draft)
    first, _ = pb.month_bounds(draft.month)
    colours = {k["key"]: k["colour"] for k in programme_record.kinds(db)}

    def running(slot: ProgrammeSlot) -> str:
        # The doc's "Sgt Davies & Cpl Malik": rank and surname.
        names = [" ".join(filter(None, [p.rank, p.last_name])) for k in slot.people or []
                 if (p := ctx.people.get(k)) and p.kind in ("staff", "nco")]
        return " & ".join(filter(None, [(slot.instructor or "").strip(), *names]))

    rooms = sorted(ctx.rooms.values(), key=lambda r: (r.position, r.name))
    used = {s.room_id for n in draft.nights for s in n.slots}
    return build_programme_pdf({
        "title": f"{first:%B %Y}",
        "notes": draft.notes,
        "key_dates": draft.key_dates or [],
        "rooms": [{"name": r.name, "show": r.active or r.id in used} for r in rooms],
        "flights": [{"key": f, "label": f"{pb.FLIGHT_LABELS[f]} Flight" if f in pb.FLIGHT_LABELS else f"{f} Flight",
                     "apart": f in pb.FLIGHT_LABELS} for f in ctx.flights],
        "nights": [{
            "date": n.date, "uniform": n.uniform, "duty_nco": n.duty_nco,
            "slots": [{"id": s.id, "period": s.period, "flights": pb.slot_flights(s, ctx.flights),
                       "title": s.title, "colour": colours.get(s.category), "running": running(s),
                       "room": ctx.rooms[s.room_id].name if s.room_id in ctx.rooms else None}
                      for s in n.slots],
        } for n in draft.nights],
    })


@router.get("/months/{month}/pdf")
def month_pdf(month: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    draft = _draft(db, month)
    pdf = _pdf(db, draft)
    first, _ = pb.month_bounds(month)
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf", headers={
        "Content-Disposition": content_disposition("attachment", f"Programme {first:%B %Y}.pdf")})


@router.post("/months/{month}/publish")
async def publish(month: str, body: PublishBody, background: BackgroundTasks, db: Session = Depends(get_db),
                  idinfo: dict = Depends(require_staff)) -> dict:
    """Send the month out: the PDF to the website, and the month to the
    programme record (replacing what the record had for it, an imported doc
    included). The website goes first: if GitHub or the PDF render fails,
    nothing is marked published and the record is untouched."""
    draft = _draft(db, month)
    if not body.website and not body.record:
        raise HTTPException(status_code=422, detail="Choose the website, the record or both")
    if not any(n.slots for n in draft.nights):
        raise HTTPException(status_code=409, detail="There's nothing on the programme to publish yet")
    first, _ = pb.month_bounds(month)
    result: dict = {}
    if body.website:
        pdf = await run_in_threadpool(_pdf, db, draft)
        result["website"] = await website.publish_pdf_to_website(pdf, first.month, first.year)
    if body.record:
        ctx = pb.build_context(db, draft)
        result["record"] = programme_record.replace_month(db, first.month, first.year, pb.record_nights(draft, ctx))
    where = " and ".join(w for w, on in (("the website", body.website), ("the record", body.record)) if on)
    _touch(draft, idinfo, f"Published to {where}")
    # After the touch, so a fresh publish reads as published, not changed.
    draft.published_at = draft.updated_at
    draft.published_by = actor_name(idinfo)
    db.commit()
    if body.notify:
        background.add_task(push.notify, "programme", f"The {first:%B} programme is out",
                            "Open it to see what's on each night", f"/programme/{month}")
    db.refresh(draft)
    return {**result, "programme": _detail(db, draft, idinfo)}


# ── bank ────────────────────────────────────────────────────────────────────

def _usage(db: Session) -> dict[str, tuple[int, date]]:
    """How often each activity is in the record, and when it was last, by name."""
    rows = (db.query(ProgrammeSession.activity, func.count(func.distinct(ProgrammeNight.id)),
                     func.max(ProgrammeNight.date))
            .join(ProgrammeNight).group_by(ProgrammeSession.activity))
    usage: dict[str, tuple[int, date]] = {}
    for activity, times, last in rows:
        key = activity.casefold()
        old = usage.get(key)
        usage[key] = (times + (old[0] if old else 0), max(filter(None, [last, old[1] if old else None])))
    return usage


@router.get("/bank")
def list_bank(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)) -> list[dict]:
    usage, people = _usage(db), pb.directory(db)
    items = db.query(ProgrammeBankItem).order_by(ProgrammeBankItem.category, ProgrammeBankItem.title).all()
    return [_bank_json(i, usage, people) for i in items]


def _check_bank(db: Session, body: BankBody) -> None:
    if body.category not in programme_record.kind_labels(db):
        raise HTTPException(status_code=422, detail="Pick a kind of session from the list")
    if body.room_id is not None and not db.get(ProgrammeRoom, body.room_id):
        raise HTTPException(status_code=422, detail="That room isn't in the list")
    if bool(body.subject_key) != bool(body.part):
        raise HTTPException(status_code=422, detail="A lesson needs both its subject and its number")
    if body.people:
        everyone = pb.directory(db)
        if any(k not in everyone or everyone[k].kind == "cadet" for k in body.people):
            raise HTTPException(status_code=422, detail="Only staff and NCOs on the roster can run a session")


@router.post("/bank", status_code=201)
def add_bank_item(body: BankBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    _check_bank(db, body)
    title = body.title.strip()
    if db.query(ProgrammeBankItem).filter(func.lower(ProgrammeBankItem.title) == title.lower()).first():
        raise HTTPException(status_code=409, detail="The bank already has a session with that name")
    item = ProgrammeBankItem(**{**body.model_dump(), "title": title, "people": list(dict.fromkeys(body.people))},
                             created_at=datetime.now(),
                             created_by=actor_name(idinfo))
    db.add(item)
    db.commit()
    return _bank_json(item, _usage(db), pb.directory(db))


@router.put("/bank/{item_id}")
def edit_bank_item(item_id: int, body: BankBody, db: Session = Depends(get_db),
                   idinfo: dict = Depends(require_staff)) -> dict:
    """Changes the bank only: sessions already placed keep what they copied."""
    item = db.get(ProgrammeBankItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="That session isn't in the bank")
    _check_bank(db, body)
    title = body.title.strip()
    clash = db.query(ProgrammeBankItem).filter(func.lower(ProgrammeBankItem.title) == title.lower(),
                                               ProgrammeBankItem.id != item_id).first()
    if clash:
        raise HTTPException(status_code=409, detail="The bank already has a session with that name")
    for name, value in {**body.model_dump(), "title": title, "people": list(dict.fromkeys(body.people))}.items():
        setattr(item, name, value)
    db.commit()
    return _bank_json(item, _usage(db), pb.directory(db))


@router.delete("/bank/{item_id}")
def delete_bank_item(item_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    item = db.get(ProgrammeBankItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="That session isn't in the bank")
    # By hand as well as ON DELETE SET NULL, which SQLite only honours with
    # foreign keys switched on.
    db.query(ProgrammeSlot).filter(ProgrammeSlot.bank_item_id == item_id).update({"bank_item_id": None})
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/bank/history")
def bank_history(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    """What the squadron has run (from the record) that isn't in the bank yet,
    most often first: the quick way to fill a new bank."""
    have = {t.casefold() for (t,) in db.query(ProgrammeBankItem.title)}
    rows = (db.query(ProgrammeSession.activity, ProgrammeSession.category,
                     func.count(func.distinct(ProgrammeNight.id)), func.max(ProgrammeNight.date))
            .join(ProgrammeNight).group_by(ProgrammeSession.activity, ProgrammeSession.category))
    best: dict[str, dict] = {}
    for activity, category, times, last in rows:
        key = activity.casefold()
        if key in have:
            continue
        entry = best.setdefault(key, {"title": activity, "category": category, "times": 0, "last_run": None,
                                      "_by": {}})
        entry["times"] += times
        entry["_by"][category] = entry["_by"].get(category, 0) + times
        entry["category"] = max(entry["_by"], key=entry["_by"].get)
        entry["last_run"] = max(filter(None, [entry["last_run"], last.isoformat() if last else None]), default=None)
    out = [{k: v for k, v in e.items() if k != "_by"} for e in best.values()]
    return sorted(out, key=lambda e: (-e["times"], e["title"]))[:100]


@router.post("/bank/import")
def import_bank(body: BankImport, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    """Add activities from the record to the bank, with the kind they were
    recorded as. Names already in the bank are skipped."""
    history = {e["title"].casefold(): e for e in bank_history(db, idinfo)}
    added = 0
    for key in dict.fromkeys(t.strip().casefold() for t in body.titles):
        entry = history.get(key)
        if not entry:
            continue
        db.add(ProgrammeBankItem(title=entry["title"][:120], category=entry["category"], created_at=datetime.now(),
                                 created_by=actor_name(idinfo)))
        added += 1
    db.commit()
    return {"added": added}


# ── kinds ───────────────────────────────────────────────────────────────────

@router.get("/kinds")
def list_kinds(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)) -> list[dict]:
    return programme_record.kinds(db)


def _label_free(db: Session, label: str, key: str | None = None) -> None:
    if any(k["label"].casefold() == label.casefold() and k["key"] != key for k in programme_record.kinds(db)):
        raise HTTPException(status_code=409, detail="There's already a kind with that name")


@router.post("/kinds", status_code=201)
def add_kind(body: KindBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    """A new kind of session. Its key comes from its name and never changes,
    so renaming it later keeps every session that has it."""
    label = body.label.strip()
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
    if not key:
        raise HTTPException(status_code=422, detail="Give the kind a name with a letter or number in it")
    _label_free(db, label)
    if key in programme_record.kind_labels(db):
        raise HTTPException(status_code=409, detail="There's already a kind with that name")
    position = (db.query(func.max(ProgrammeKind.position)).scalar() or 0) + 1
    db.add(ProgrammeKind(key=key, label=label, colour=body.colour, hidden=body.hidden, position=position))
    db.commit()
    return programme_record.kinds(db)


@router.put("/kinds/{key}")
def edit_kind(key: str, body: KindBody, db: Session = Depends(get_db),
              idinfo: dict = Depends(require_staff)) -> list[dict]:
    """Rename, colour or hide a kind, built-in ones included. Hiding takes it
    out of the pickers; sessions that have it keep it."""
    if key not in programme_record.kind_labels(db):
        raise HTTPException(status_code=404, detail="That kind isn't in the list")
    label = body.label.strip()
    _label_free(db, label, key)
    row = db.get(ProgrammeKind, key) or ProgrammeKind(key=key, position=0)
    row.label, row.colour, row.hidden = label, body.colour, body.hidden
    db.merge(row)
    db.commit()
    return programme_record.kinds(db)


@router.delete("/kinds/{key}")
def delete_kind(key: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    """Only a kind staff added and nothing has: the built-in ones and any in
    use are hidden instead, so no session or past night loses its kind."""
    if key in programme_record.CATEGORIES:
        raise HTTPException(status_code=409, detail="That kind is built in: hide it instead")
    row = db.get(ProgrammeKind, key)
    if not row:
        raise HTTPException(status_code=404, detail="That kind isn't in the list")
    used = any(db.query(model).filter(model.category == key).first()
               for model in (ProgrammeSlot, ProgrammeBankItem, ProgrammeSession))
    if used:
        raise HTTPException(status_code=409, detail="Sessions have that kind: hide it instead")
    db.delete(row)
    db.commit()
    return programme_record.kinds(db)


# ── rooms ───────────────────────────────────────────────────────────────────

@router.get("/rooms")
def list_rooms(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)) -> list[dict]:
    return [_room_json(r) for r in db.query(ProgrammeRoom).order_by(ProgrammeRoom.position, ProgrammeRoom.name)]


def _room_name_free(db: Session, name: str, room_id: int | None = None) -> None:
    query = db.query(ProgrammeRoom).filter(func.lower(ProgrammeRoom.name) == name.lower())
    if room_id:
        query = query.filter(ProgrammeRoom.id != room_id)
    if query.first():
        raise HTTPException(status_code=409, detail="There's already a room with that name")


@router.post("/rooms", status_code=201)
def add_room(body: RoomBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    name = body.name.strip()
    _room_name_free(db, name)
    position = (db.query(func.max(ProgrammeRoom.position)).scalar() or 0) + 1
    room = ProgrammeRoom(name=name, capacity=body.capacity, notes=(body.notes or "").strip() or None,
                         active=body.active, position=position)
    db.add(room)
    db.commit()
    return _room_json(room)


@router.put("/rooms/{room_id}")
def edit_room(room_id: int, body: RoomBody, db: Session = Depends(get_db),
              idinfo: dict = Depends(require_staff)) -> dict:
    room = db.get(ProgrammeRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    name = body.name.strip()
    _room_name_free(db, name, room_id)
    room.name, room.capacity, room.active = name, body.capacity, body.active
    room.notes = (body.notes or "").strip() or None
    db.commit()
    return _room_json(room)


@router.delete("/rooms/{room_id}")
def delete_room(room_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    """Sessions in the room keep their place and lose the room, which the
    checks then point out."""
    room = db.get(ProgrammeRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    db.query(ProgrammeSlot).filter(ProgrammeSlot.room_id == room_id).update({"room_id": None})
    db.query(ProgrammeBankItem).filter(ProgrammeBankItem.room_id == room_id).update({"room_id": None})
    db.delete(room)
    db.commit()
    return {"ok": True}


# ── people ──────────────────────────────────────────────────────────────────

@router.get("/people")
def people(month: str | None = None, db: Session = Depends(get_db),
           idinfo: dict = Depends(require_staff_or_nco)) -> list[dict]:
    """Who can go on a session (staff) or be @'d (everyone). NCOs get staff
    and NCOs only, without addresses: they only need names to mention."""
    is_staff = _is_staff(idinfo)
    everyone = pb.directory(db)
    away: dict = {}
    if month:
        try:
            first, after = pb.month_bounds(month)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        away = pb.holidays(db, everyone, first, after)
    out = []
    for p in everyone.values():
        if not is_staff and not p.mentionable:
            continue
        out.append({"key": p.key, "name": p.name, "kind": p.kind, "flight": p.flight,
                    "mentionable": p.mentionable, "email": p.email if is_staff else None,
                    "away": [[a.isoformat(), b.isoformat()] for a, b in away.get(p.key, [])]})
    return out


# ── comments ────────────────────────────────────────────────────────────────

def _comment(db: Session, comment_id: int) -> ProgrammeComment:
    c = db.get(ProgrammeComment, comment_id)
    if not c:
        raise HTTPException(status_code=404, detail="Comment not found")
    return c


def _thread_people(root: ProgrammeComment) -> set[str]:
    keys = set(root.mentions or [])
    for r in root.replies:
        keys |= set(r.mentions or [])
    if root.assignee:
        keys.add(root.assignee)
    return keys


def _notify(comment_id: int, event: str, actor_email: str, actor: str, new_mentions: list[str],
            assigned: str | None = None) -> None:
    """Email (and push) everyone a comment concerns, as Docs does: whoever was
    just @'d gets "mentioned you", whoever was just assigned gets "assigned
    you", and for a reply, resolve or reopen everyone else in the thread gets
    that. The person who did it never gets their own email. Runs after the
    response, with its own session; a failure is logged, never raised."""
    try:
        with database.SessionLocal() as db:
            c = db.get(ProgrammeComment, comment_id)
            if not c:
                return
            root = c.parent or c
            people = pb.directory(db)
            # Named as the roster names them, like the thread itself.
            me_on_roster = pb.person_for_email(people, actor_email)
            actor = me_on_roster.name if me_on_roster else actor
            month = root.programme.month
            name = _programme_name(month)
            link = f"{SITE_BASE_URL}/programme/{month}?comment={root.id}"
            thread = [root, *root.replies]

            def author(x: ProgrammeComment) -> str:
                person = pb.person_for_email(people, x.author.email)
                return person.name if person else _user_name(x.author)

            lines = [(author(x), pb.plain_mentions(x.body), x.id == c.id) for x in thread]
            assignee = people[root.assignee].name if root.assignee in people else None
            me = (actor_email or "").lower()
            sent: set[str] = {me}
            recipients: list[tuple[str, str]] = []  # (email, kind)

            def add(email: str | None, kind: str) -> None:
                if email and email.lower() not in sent:
                    sent.add(email.lower())
                    recipients.append((email, kind))

            if assigned and assigned in people:
                add(people[assigned].email, "assigned")
            for key in new_mentions:
                if key in people:
                    add(people[key].email, "mention")
            if event in ("reply", "resolved", "reopened"):
                for x in thread:
                    add(x.author.email, event)
                for key in _thread_people(root):
                    if key in people:
                        add(people[key].email, event)

            for email, kind in recipients:
                send_email(email, programme_comment_subject(kind, actor, name),
                           programme_comment_email_html(kind, actor, name, root.quote, lines, link, assignee))
            if recipients:
                kinds = {k for _, k in recipients}
                title = f"{actor} {'mentioned you' if kinds == {'mention'} else 'commented'} on the {name}"
                push.notify("programme", title, pb.plain_mentions(c.body)[:140],
                            f"/programme/{month}?comment={root.id}", emails=[e for e, _ in recipients])
    except Exception:
        logger.exception("programme comment notification failed")


@router.post("/months/{month}/comments", status_code=201)
def add_comment(month: str, body: CommentBody, background: BackgroundTasks, db: Session = Depends(get_db),
                idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    """Start a thread (on the programme, a night or a session) or reply to
    one. A reply to a resolved thread reopens it, as in Docs."""
    draft = _draft(db, month)
    user = get_or_create_user(db, idinfo)
    people = pb.directory(db)
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write something first")
    mentions = pb.parse_mentions(text, people)
    if body.assign and not mentions:
        raise HTTPException(status_code=422, detail="@ someone to assign it to them")
    now = datetime.now()
    comment = ProgrammeComment(programme_id=draft.id, author_id=user.id, body=text, mentions=mentions,
                               created_at=now)
    event = "comment"
    assigned = None
    if body.parent_id is not None:
        parent = _comment(db, body.parent_id)
        root = parent.parent or parent
        if root.programme_id != draft.id:
            raise HTTPException(status_code=404, detail="Comment not found")
        comment.parent_id = root.id
        event = "reply"
        if root.resolved_at:
            root.resolved_at, root.resolved_by = None, None
        if body.assign:
            root.assignee = assigned = mentions[0]
    else:
        flights = pb.flights(db)
        if body.slot_id is not None:
            slot = db.get(ProgrammeSlot, body.slot_id)
            if not slot or slot.night.programme_id != draft.id:
                raise HTTPException(status_code=404, detail="Session not found")
            comment.slot_id, comment.night_id = slot.id, slot.night_id
            comment.quote = pb.slot_quote(slot, flights)
        elif body.night_id is not None:
            night = db.get(ProgrammeDraftNight, body.night_id)
            if not night or night.programme_id != draft.id:
                raise HTTPException(status_code=404, detail="Night not found")
            comment.night_id = night.id
            comment.quote = f"{night.date:%A} {night.date.day} {night.date:%B}"
        else:
            comment.quote = _programme_name(month).capitalize()
        if body.assign:
            comment.assignee = assigned = mentions[0]
    db.add(comment)
    db.commit()
    background.add_task(_notify, comment.id, event, user.email, actor_name(idinfo) or _user_name(user),
                        [k for k in mentions if k != assigned], assigned)
    return _detail(db, draft, idinfo)


@router.patch("/comments/{comment_id}")
def edit_comment(comment_id: int, body: EditComment, background: BackgroundTasks, db: Session = Depends(get_db),
                 idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    """The author fixes their comment. Someone newly @'d is emailed; anyone
    already mentioned isn't emailed again."""
    c = _comment(db, comment_id)
    user = get_or_create_user(db, idinfo)
    if c.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the person who wrote it can edit a comment")
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write something first")
    people = pb.directory(db)
    mentions = pb.parse_mentions(text, people)
    new = [k for k in mentions if k not in (c.mentions or [])]
    c.body, c.mentions, c.edited_at = text, mentions, datetime.now()
    db.commit()
    if new:
        background.add_task(_notify, c.id, "edit", user.email, actor_name(idinfo) or _user_name(user), new)
    return _detail(db, c.programme, idinfo)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, db: Session = Depends(get_db),
                   idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    """The author, or staff, removes a comment; removing the first comment
    removes the thread."""
    c = _comment(db, comment_id)
    user = get_or_create_user(db, idinfo)
    if c.author_id != user.id and not _is_staff(idinfo):
        raise HTTPException(status_code=403, detail="You can only delete your own comments")
    draft = c.programme
    db.delete(c)
    db.commit()
    db.refresh(draft)
    return _detail(db, draft, idinfo)


def _resolve(comment_id: int, resolved: bool, background: BackgroundTasks, db: Session, idinfo: dict) -> dict:
    c = _comment(db, comment_id)
    if c.parent_id is not None:
        raise HTTPException(status_code=422, detail="Resolve the thread, not a reply")
    if bool(c.resolved_at) == resolved:
        raise HTTPException(status_code=409, detail="It's already " + ("resolved" if resolved else "open"))
    user = get_or_create_user(db, idinfo)
    c.resolved_at = datetime.now() if resolved else None
    me = pb.person_for_email(pb.directory(db), user.email)
    c.resolved_by = (me.name if me else actor_name(idinfo) or _user_name(user)) if resolved else None
    db.commit()
    background.add_task(_notify, c.id, "resolved" if resolved else "reopened", user.email,
                        actor_name(idinfo) or _user_name(user), [])
    return _detail(db, c.programme, idinfo)


@router.post("/comments/{comment_id}/resolve")
def resolve_comment(comment_id: int, background: BackgroundTasks, db: Session = Depends(get_db),
                    idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    return _resolve(comment_id, True, background, db, idinfo)


@router.post("/comments/{comment_id}/reopen")
def reopen_comment(comment_id: int, background: BackgroundTasks, db: Session = Depends(get_db),
                   idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    return _resolve(comment_id, False, background, db, idinfo)
