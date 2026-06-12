"""Parade-night text management — generation from the programme doc,
message editing/approval, recipients, and sending via GOV.UK Notify."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import extract
from sqlalchemy.orm import Session

from database.models import ParadeNightMessage, SmsRecipient

from core.db import get_db
from core.security import require_staff
from texts.ai import PRIMARY_MODEL, format_uniform, generate_message, model_label
from texts.programme_parser import parse_programme
from texts.sender import send_parade_message, send_test_sms

router = APIRouter(prefix="/texts")


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


# ─── Messages ─────────────────────────────────────────────────────────────────

@router.post("/generate")
def generate_messages(  # sync on purpose — slow AI calls run in the threadpool
    month: int = None,
    year: int = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    now = datetime.now()
    month = month or now.month
    year = year or now.year

    try:
        nights = parse_programme(month, year)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read programme doc: {e}")

    generated, skipped = 0, 0
    model_counts: dict[str, int] = {}
    for night in nights:
        existing = (
            db.query(ParadeNightMessage)
            .filter(ParadeNightMessage.parade_date == night["date"])
            .first()
        )
        if existing and existing.status == "sent":
            skipped += 1
            continue

        try:
            main_message, c_message, model_id = generate_message(night["main_body"], night["c_flight"])
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

        if not existing:
            existing = ParadeNightMessage(parade_date=night["date"])
            db.add(existing)

        existing.uniform = format_uniform(night["uniform"])
        existing.uniform_raw = night["uniform"]
        existing.dnco = night["dnco"]
        existing.c_flight_raw = night["c_flight"]
        existing.main_body_raw = night["main_body"]
        existing.main_message = main_message
        existing.c_flight_message = c_message
        existing.status = "draft"
        existing.generated_by = model_id
        existing.generated_at = datetime.now()
        generated += 1
        model_counts[model_id] = model_counts.get(model_id, 0) + 1

    db.commit()
    return {
        "status": "success",
        "generated": generated,
        "skipped_sent": skipped,
        "models_used": [
            {"model": mid, "label": model_label(mid), "count": count,
             "fallback": mid != PRIMARY_MODEL}
            for mid, count in model_counts.items()
        ],
    }


@router.get("/messages")
async def list_messages(
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
async def update_message(
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


@router.post("/messages/{message_id}/regenerate")
def regenerate_message(  # sync on purpose — slow AI call runs in the threadpool
    message_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    message = _get_message(db, message_id)
    if message.status == "sent":
        raise HTTPException(status_code=400, detail="Message has already been sent")

    try:
        main_message, c_message, model_id = generate_message(message.main_body_raw, message.c_flight_raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    message.main_message = main_message
    message.c_flight_message = c_message
    message.uniform = format_uniform(message.uniform_raw)
    message.status = "draft"
    message.generated_by = model_id
    message.generated_at = datetime.now()
    db.commit()
    return _message_json(message)


@router.post("/messages/{message_id}/send")
async def send_message(
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
async def test_send_message(
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

class RecipientBody(BaseModel):
    rank: str = ""
    surname: str = ""
    phone_number: str


class RecipientPatch(BaseModel):
    rank: Optional[str] = None
    surname: Optional[str] = None
    phone_number: Optional[str] = None


def _recipient_json(r: SmsRecipient) -> dict:
    return {"id": r.id, "rank": r.rank, "surname": r.surname, "phone_number": r.phone_number}


@router.get("/recipients")
async def list_recipients(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    recipients = db.query(SmsRecipient).order_by(SmsRecipient.surname).all()
    return [_recipient_json(r) for r in recipients]


@router.post("/recipients")
async def create_recipient(
    data: RecipientBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    phone = data.phone_number.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    recipient = SmsRecipient(rank=data.rank.strip(), surname=data.surname.strip(), phone_number=phone)
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return _recipient_json(recipient)


@router.patch("/recipients/{recipient_id}")
async def update_recipient(
    recipient_id: int,
    data: RecipientPatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    recipient = db.query(SmsRecipient).get(recipient_id)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    for field in ("rank", "surname", "phone_number"):
        val = getattr(data, field)
        if val is not None:
            setattr(recipient, field, val.strip())

    if not recipient.phone_number:
        raise HTTPException(status_code=400, detail="Phone number is required")

    db.commit()
    return _recipient_json(recipient)


@router.delete("/recipients/{recipient_id}")
async def delete_recipient(
    recipient_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    recipient = db.query(SmsRecipient).get(recipient_id)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    db.delete(recipient)
    db.commit()
    return {"status": "success"}
