"""Parade-night text management — generation from the programme doc,
message editing/approval, recipients, and sending via GOV.UK Notify."""

import asyncio
import csv
import io
import json
from datetime import date, datetime
from typing import Literal, Optional

import openpyxl
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import extract
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from database.database import SessionLocal
from database.models import Cadet, ParadeNightMessage, SmsRecipient, Staff

from core.db import get_db
from core.security import require_staff
from texts.ai import PRIMARY_MODEL, format_uniform, generate_message, model_label
from texts.matching import Roster
from texts.phone import clean_mobile, normalise_phone
from texts.programme_parser import parse_programme
from texts.recipients import (
    Recipient, extra_recipient, list_recipients, parse_key, person_recipient,
)
from texts.sender import send_parade_message, send_test_sms

router = APIRouter(prefix="/texts")

# How many nights are written at once. The AI call is the whole cost of a month's
# generation and the free tiers cap requests per *minute* rather than in flight,
# so a handful in parallel finishes several times sooner; core.llm already waits
# out a 429, so overshooting the limit costs a pause, not a failure.
AI_CONCURRENCY = 4

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def _message_json(m: ParadeNightMessage) -> dict:
    return {
        "id": m.id,
        "parade_date": m.parade_date.isoformat(),
        "uniform": m.uniform,
        "uniform_raw": m.uniform_raw,
        "dnco": m.dnco,
        "c_flight_raw": m.c_flight_raw,
        "main_body_raw": m.main_body_raw,
        "main_message": m.main_message,
        "c_flight_message": m.c_flight_message,
        "status": m.status,
        "generated_by": m.generated_by,
        "generated_by_label": model_label(m.generated_by) if m.generated_by else None,
        "generated_with_fallback": bool(m.generated_by) and m.generated_by != PRIMARY_MODEL,
        "generated_at": m.generated_at.isoformat() if m.generated_at else None,
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
        "send_results": m.send_results,
    }


def _get_message(db: Session, message_id: int) -> ParadeNightMessage:
    message = db.query(ParadeNightMessage).get(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


# ─── Generation ───────────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _load_programme(month: int | None, year: int | None) -> list[dict]:
    """Parse a month's programme doc off the event loop, turning its failures into
    HTTP errors. Callers do this *before* they start streaming, so a missing doc is
    still a real status code rather than an error buried inside a 200."""
    now = datetime.now()
    try:
        return await run_in_threadpool(parse_programme, month or now.month, year or now.year)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read programme doc: {e}")


def _apply_night(message: ParadeNightMessage, night: dict) -> None:
    """Copy a parsed programme entry onto a message — everything except the AI text."""
    message.uniform_raw = night["uniform"]
    message.dnco = night["dnco"]
    message.c_flight_raw = night["c_flight"]
    message.main_body_raw = night["main_body"]


def _apply_generated(message: ParadeNightMessage, main_message: str, c_message: str,
                     model_id: str, part: str = "all") -> None:
    """Write an AI result onto a message.

    ``part`` narrows it to one of the two texts so a main message you're happy with
    survives a C Flight rewrite (and vice versa). The uniform line isn't written by
    the AI at all — it's derived from the raw programme text — so it's only reset on
    a full regeneration, where any hand edit to it is being replaced anyway.
    """
    if part in ("all", "main"):
        message.main_message = main_message
    if part in ("all", "c_flight"):
        message.c_flight_message = c_message
    if part == "all":
        message.uniform = format_uniform(message.uniform_raw)
    message.status = "draft"
    message.generated_by = model_id
    message.generated_at = datetime.now()


async def _generate_nights(db: Session, nights: list[dict]):
    """Write a text for each parade night, yielding one event as each lands:
    ``message`` for a fresh draft, ``skipped`` for a night already sent, ``error``
    for one the AI wouldn't write.

    The AI calls are blocking httpx, so they run in worker threads several at a
    time, and every result is applied back here on the event loop — the Session is
    only ever touched from one thread. Events therefore come out in *completion*
    order, not date order: callers pass them straight on and let the UI put the
    cards in their place, which is what lets the first finished night appear while
    the rest are still being written.
    """
    todo: list[tuple[dict, ParadeNightMessage | None]] = []
    for night in nights:
        existing = (
            db.query(ParadeNightMessage)
            .filter(ParadeNightMessage.parade_date == night["date"])
            .first()
        )
        if existing is not None and existing.status == "sent":
            yield {"type": "skipped", "parade_date": night["date"].isoformat()}
            continue
        todo.append((night, existing))

    semaphore = asyncio.Semaphore(AI_CONCURRENCY)

    async def write(night: dict, existing: ParadeNightMessage | None):
        async with semaphore:
            try:
                result = await asyncio.to_thread(
                    generate_message, night["main_body"], night["c_flight"]
                )
                return night, existing, result, None
            except Exception as e:
                # One night the model chokes on shouldn't lose the whole month —
                # report it and let the others through.
                return night, existing, None, e

    tasks = [asyncio.create_task(write(night, existing)) for night, existing in todo]
    try:
        for finished in asyncio.as_completed(tasks):
            night, message, result, error = await finished
            if error is not None:
                yield {"type": "error", "parade_date": night["date"].isoformat(),
                       "error": str(error)}
                continue

            if message is None:
                message = ParadeNightMessage(parade_date=night["date"])
                db.add(message)
            _apply_night(message, night)
            _apply_generated(message, *result)
            # Commit per night, so a run the user navigates away from still leaves
            # the nights already written saved.
            db.commit()
            yield {"type": "message", "message": _message_json(message)}
    finally:
        for task in tasks:
            task.cancel()


def _new_totals() -> dict:
    return {"generated": 0, "skipped_sent": 0, "failed": 0, "models": {}, "first_error": None}


def _tally(totals: dict, event: dict) -> None:
    """Fold one generation event into the counts both generate endpoints report."""
    if event["type"] == "message":
        totals["generated"] += 1
        model = event["message"]["generated_by"]
        totals["models"][model] = totals["models"].get(model, 0) + 1
    elif event["type"] == "skipped":
        totals["skipped_sent"] += 1
    elif event["type"] == "error":
        totals["failed"] += 1
        totals["first_error"] = totals["first_error"] or event["error"]


def _summary(totals: dict) -> dict:
    return {
        "status": "success",
        "generated": totals["generated"],
        "skipped_sent": totals["skipped_sent"],
        "failed": totals["failed"],
        "models_used": [
            {"model": mid, "label": model_label(mid), "count": count,
             "fallback": mid != PRIMARY_MODEL}
            for mid, count in totals["models"].items()
        ],
    }


@router.post("/generate")
async def generate_messages(
    month: int = None,
    year: int = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Generate the whole month and answer once it's done. /generate/stream is what
    the UI calls; this stays for anything that only wants the summary."""
    nights = await _load_programme(month, year)

    totals = _new_totals()
    async for event in _generate_nights(db, nights):
        _tally(totals, event)

    # Every night failing is the AI being down, not a per-night problem — say so
    # with a status code rather than a cheerful "generated 0".
    if totals["generated"] == 0 and totals["failed"] > 0:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {totals['first_error']}")
    return _summary(totals)


@router.post("/generate/stream")
async def generate_messages_stream(
    month: int = None,
    year: int = None,
    idinfo: dict = Depends(require_staff),
):
    """The same generation as /generate, but each night is sent the moment it's
    written instead of after the last one — the first parade night is on screen
    while the rest are still running.

    Server-sent events over POST: EventSource can't set an Authorization header and
    an id_token in the query string lands in every access log, so the frontend
    reads the body stream itself. text/event-stream is also the one content type
    GZipMiddleware leaves alone — gzip would sit on each event until it had enough
    to compress, which is exactly what we're trying to avoid.
    """
    nights = await _load_programme(month, year)

    async def events():
        # Our own Session: FastAPI closes a Depends(get_db) one before the response
        # body starts streaming, so this generator can't borrow it.
        db = SessionLocal()
        totals = _new_totals()
        try:
            yield _sse({"type": "start",
                        "parade_dates": [n["date"].isoformat() for n in nights]})
            async for event in _generate_nights(db, nights):
                _tally(totals, event)
                yield _sse(event)
            yield _sse({"type": "done", **_summary(totals)})
        except Exception as e:
            # Headers went out with the 200, so a late failure can only be reported
            # in the stream itself.
            db.rollback()
            yield _sse({"type": "fatal", "error": str(e)})
        finally:
            db.close()

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


class GenerateNightBody(BaseModel):
    parade_date: date
    # Which of the two texts to write — see _apply_generated.
    part: Literal["all", "main", "c_flight"] = "all"


@router.post("/generate/night")
async def generate_night(
    data: GenerateNightBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Generate a single parade night straight from the programme doc — for a night
    a batch run failed on, or one whose programme entry has changed since it was
    last written. Unlike /messages/{id}/regenerate this re-reads the doc, and it
    creates the message if there isn't one yet."""
    nights = await _load_programme(data.parade_date.month, data.parade_date.year)
    night = next((n for n in nights if n["date"].date() == data.parade_date), None)
    if night is None:
        raise HTTPException(status_code=404, detail="That date isn't a parade night in the programme")

    message = (
        db.query(ParadeNightMessage)
        .filter(ParadeNightMessage.parade_date == night["date"])
        .first()
    )
    if message is not None and message.status == "sent":
        raise HTTPException(status_code=400, detail="Message has already been sent")

    try:
        result = await asyncio.to_thread(generate_message, night["main_body"], night["c_flight"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    if message is None:
        message = ParadeNightMessage(parade_date=night["date"])
        db.add(message)
    _apply_night(message, night)
    _apply_generated(message, *result, part=data.part)
    db.commit()
    return _message_json(message)


# ─── Messages ─────────────────────────────────────────────────────────────────

@router.get("/messages")
def list_messages(
    month: int = None,
    year: int = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    now = datetime.now()
    month = month or now.month
    year = year or now.year

    messages = (
        db.query(ParadeNightMessage)
        .filter(
            extract("month", ParadeNightMessage.parade_date) == month,
            extract("year", ParadeNightMessage.parade_date) == year,
        )
        .order_by(ParadeNightMessage.parade_date)
        .all()
    )
    return [_message_json(m) for m in messages]


class MessagePatch(BaseModel):
    uniform: Optional[str] = None
    dnco: Optional[str] = None
    main_message: Optional[str] = None
    c_flight_message: Optional[str] = None
    status: Optional[str] = None  # "draft" | "ready"


@router.patch("/messages/{message_id}")
def update_message(
    message_id: int,
    data: MessagePatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    message = _get_message(db, message_id)
    if message.status == "sent":
        raise HTTPException(status_code=400, detail="Message has already been sent")

    if data.status is not None and data.status not in ("draft", "ready"):
        raise HTTPException(status_code=400, detail="Status must be 'draft' or 'ready'")

    for field in ("uniform", "dnco", "main_message", "c_flight_message", "status"):
        val = getattr(data, field)
        if val is not None:
            setattr(message, field, val)

    db.commit()
    return _message_json(message)


class RegenerateBody(BaseModel):
    # Which of the two texts to rewrite — see _apply_generated.
    part: Literal["all", "main", "c_flight"] = "all"


@router.post("/messages/{message_id}/regenerate")
async def regenerate_message(
    message_id: int,
    data: Optional[RegenerateBody] = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Rewrite one night from the programme text already stored on it. No Google
    Docs round trip — /generate/night is the one that re-reads the doc."""
    message = _get_message(db, message_id)
    if message.status == "sent":
        raise HTTPException(status_code=400, detail="Message has already been sent")

    try:
        result = await asyncio.to_thread(
            generate_message, message.main_body_raw, message.c_flight_raw
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    _apply_generated(message, *result, part=data.part if data else "all")
    db.commit()
    return _message_json(message)


@router.post("/messages/{message_id}/send")
def send_message(
    message_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    message = _get_message(db, message_id)
    if message.status == "sent":
        raise HTTPException(status_code=400, detail="Message has already been sent")

    try:
        results = send_parade_message(db, message)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    failed = [r for r in results if r["status"] == "failed"]
    return {
        "status": "success",
        "sent": len(results) - len(failed),
        "failed": len(failed),
        "message": _message_json(message),
    }


class TestSendBody(BaseModel):
    phone_number: str


@router.post("/messages/{message_id}/test-send")
def test_send_message(
    message_id: int,
    data: TestSendBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    message = _get_message(db, message_id)
    try:
        send_test_sms(message, data.phone_number.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Test send failed: {e}")
    return {"status": "success"}


# ─── Recipients ───────────────────────────────────────────────────────────────
#
# A recipient is a cadet or staff member with a mobile on their record, or an
# "extra" row for someone with no account (parents, mainly). They're addressed by
# the key texts.recipients builds — "cadet:1234567", "staff:1234567", "extra:12"
# — because there is no single table to hold an id against any more. Clearing a
# cadet's or staff member's number takes them off the list; only extras are ever
# deleted outright.


class RecipientBody(BaseModel):
    rank: str = ""
    surname: str = ""
    phone_number: str


class RecipientPatch(BaseModel):
    rank: Optional[str] = None
    surname: Optional[str] = None
    phone_number: Optional[str] = None


def _recipient_json(r: Recipient) -> dict:
    return {
        "key": r.key,
        "source": r.source,
        "rank": r.rank,
        "surname": r.surname,
        "name": r.name,
        "phone_number": r.phone_number,
        # The CIN, for the cadet/staff page this recipient came from.
        "cin": r.id if r.source in ("cadet", "staff") else None,
    }


def _clean_phone(value: str) -> str:
    """A typed number, or a 400 saying why not."""
    try:
        return clean_mobile(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _resolve(db: Session, key: str):
    """The row a recipient key points at."""
    try:
        source, row_id = parse_key(key)
    except ValueError:
        raise HTTPException(status_code=404, detail="Recipient not found")

    model = {"cadet": Cadet, "staff": Staff, "extra": SmsRecipient}[source]
    column = SmsRecipient.id if source == "extra" else model.cin
    row = db.query(model).filter(column == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Recipient not found")
    return source, row


@router.get("/recipients")
def get_recipients(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return [_recipient_json(r) for r in list_recipients(db)]


@router.post("/recipients")
def create_recipient(
    data: RecipientBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Add someone with no account — a parent, say. Cadets and staff get a
    number by saving one on their own record instead."""
    phone = _clean_phone(data.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    recipient = SmsRecipient(rank=data.rank.strip(), surname=data.surname.strip(), phone_number=phone)
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return _recipient_json(extra_recipient(recipient))


@router.patch("/recipients/{key}")
def update_recipient(
    key: str,
    data: RecipientPatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    source, row = _resolve(db, key)

    if data.phone_number is not None:
        phone = _clean_phone(data.phone_number)
        if source == "extra" and not phone:
            raise HTTPException(status_code=400, detail="Phone number is required")
        # An empty number on a cadet or staff member is how they come off the
        # list, so it's stored as NULL rather than "" and read back as neither.
        row.phone_number = phone or None

    # Rank and surname come from the roster for anyone who's on it — editing them
    # here would just be overwritten by the next scrape.
    if source == "extra":
        for field in ("rank", "surname"):
            val = getattr(data, field)
            if val is not None:
                setattr(row, field, val.strip())

    db.commit()
    db.refresh(row)
    return _recipient_json(
        extra_recipient(row) if source == "extra" else person_recipient(source, row)
    )


@router.delete("/recipients/{key}")
def delete_recipient(
    key: str,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Take someone off the list. An extra row goes; a cadet or staff member
    keeps their record and just loses the number."""
    source, row = _resolve(db, key)

    if source == "extra":
        db.delete(row)
    else:
        row.phone_number = None

    db.commit()
    return {"status": "success"}


def _parse_recipient_file(filename: str, content: bytes) -> list[list]:
    """Return raw rows (including header) from a .csv / tab-separated / .xlsx file."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
        wb.close()
        return rows

    text = content.decode("utf-8-sig", errors="replace")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in first_line else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


@router.post("/recipients/import")
async def import_recipients(
    file: UploadFile = File(...),
    mode: str = Form("merge"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Load a rank/surname/phone sheet, putting each number on the cadet or staff
    member it names and keeping the rest as extras.

    Matching is the same one the old list was migrated with (texts.matching), so
    a re-import lands the same way the migration did.
    """
    if mode not in ("replace", "merge"):
        raise HTTPException(status_code=400, detail="Mode must be 'replace' or 'merge'")

    content = await file.read()
    try:
        rows = _parse_recipient_file(file.filename or "", content)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read the file — use a .csv or .xlsx export")

    if not rows:
        raise HTTPException(status_code=400, detail="The file is empty")

    header = [str(h or "").strip().lower() for h in rows[0]]
    if "phone number" not in header:
        raise HTTPException(status_code=400, detail="The file needs a 'phone number' column")
    phone_i = header.index("phone number")
    rank_i = header.index("rank") if "rank" in header else None
    surname_i = header.index("surname") if "surname" in header else None

    def cell(row: list, i: int | None) -> str:
        if i is None or i >= len(row):
            return ""
        return str(row[i] or "").strip()

    parsed: dict[str, dict] = {}  # keyed by normalised phone, last row wins
    skipped = 0
    for row in rows[1:]:
        phone = normalise_phone(row[phone_i] if phone_i < len(row) else "")
        if not phone:
            if any(str(c or "").strip() for c in row):
                skipped += 1
            continue
        parsed[phone] = {"rank": cell(row, rank_i), "surname": cell(row, surname_i)}

    if not parsed:
        raise HTTPException(status_code=400, detail="No rows with a phone number found")

    if mode == "replace":
        # Only the extras are wiped — a replace shouldn't quietly strip the
        # numbers cadets and staff set for themselves.
        db.query(SmsRecipient).delete()
        existing = {}
    else:
        existing = {
            normalise_phone(r.phone_number): r
            for r in db.query(SmsRecipient).all()
        }

    roster = Roster(db)
    matched = 0
    for phone, fields in parsed.items():
        match = roster.match(fields["rank"], fields["surname"])
        if match.matched:
            match.person.phone_number = phone
            matched += 1
            # The same person may also be sitting in the extras list under this
            # number — drop it, or they'd stay behind as a duplicate row.
            stale = existing.pop(phone, None)
            if stale is not None:
                db.delete(stale)
            continue

        recipient = existing.get(phone)
        if recipient:
            recipient.rank = fields["rank"]
            recipient.surname = fields["surname"]
        else:
            db.add(SmsRecipient(phone_number=phone, rank=fields["rank"], surname=fields["surname"]))

    db.commit()
    return {
        "status": "success",
        "imported": len(parsed),
        "matched": matched,
        "extras": len(parsed) - matched,
        "skipped": skipped,
        "total": len(list_recipients(db)),
    }
