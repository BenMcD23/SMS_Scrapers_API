"""Cadet records — search, list, detail, and staff edits."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.models import Cadet

from core.db import get_db
from core.security import require_staff, require_staff_or_nco

router = APIRouter()


class CadetPatch(BaseModel):
    email: Optional[str] = None
    banned: Optional[bool] = None


def _cadet_summary(c: Cadet) -> dict:
    return {
        "cin":        c.cin,
        "first_name": c.first_name,
        "last_name":  c.last_name,
        "rank":       c.rank,
        "flight":     c.flight,
    }


@router.get("/cadets/search")
async def search_cadets(
    q: str = "",
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    if not q or len(q.strip()) < 2:
        return []

    search = f"%{q.strip()}%"
    cadets = db.query(Cadet).filter(
        or_(
            Cadet.first_name.ilike(search),
            Cadet.last_name.ilike(search),
            (Cadet.first_name + " " + Cadet.last_name).ilike(search),
        )
    ).order_by(Cadet.last_name, Cadet.first_name).limit(10).all()

    return [_cadet_summary(c) for c in cadets]


@router.get("/cadets")
async def list_cadets(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    return [_cadet_summary(c) for c in cadets]


@router.get("/cadets/{cin}")
async def get_cadet(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    qualifications = [
        {
            "id": q.id,
            "qualification_name": q.qual_type.replace("_", " ").title(),
            "achieved_date": q.date_achieved.isoformat() if q.date_achieved else None,
            "expires_date": q.date_expires.isoformat() if q.date_expires else None,
            "status": q.status,
        }
        for q in cadet.qualifications
    ]

    events = [
        {
            "id": e.id,
            "event_name": e.event.title if e.event else f"Event {e.event_id}",
            "event_date": None,
            "attended": True,
        }
        for e in cadet.cadet_events
    ]

    assessments = [
        {
            "id": a.id,
            "assessment_type": a.assessment_type,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "passed": a.fields.get("passed") if a.fields else None,
            "total_score": a.fields.get("total_score") if a.fields else None,
            "exercise_name": a.fields.get("exercise_name") if a.fields else None,
            "assessor_name": a.fields.get("assessor_name") if a.fields else None,
        }
        for a in cadet.assessment_sheets
    ]

    return {
        **_cadet_summary(cadet),
        "email": cadet.email,
        "date_of_birth": cadet.date_of_birth.isoformat() if cadet.date_of_birth else None,
        "banned": cadet.banned,
        "qualifications": qualifications,
        "events": events,
        "assessments": assessments,
    }


@router.patch("/cadets/{cin}")
async def patch_cadet(
    cin: int,
    data: CadetPatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    # Only update fields that were explicitly provided
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cadet, field, value)

    db.commit()
    db.refresh(cadet)

    return {"status": "success", "message": f"Cadet {cin} updated.", "updated_fields": list(update_data.keys())}
