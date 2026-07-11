"""Inspection marking sheet — scraped absences for a parade date, and sheet
submission with AWOL detection (marked absent but no absence log)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from database.models import CadetAbsence, InspectionSheet
from core.db import get_db
from core.security import require_staff

router = APIRouter()


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Bad date, expected YYYY-MM-DD")


def _absent_cins_on(db: Session, date: datetime) -> set[int]:
    rows = (
        db.query(CadetAbsence.cadet_id)
        .filter(and_(CadetAbsence.date_from <= date, CadetAbsence.date_to >= date))
        .all()
    )
    return {r[0] for r in rows}


@router.get("/absences")
async def absences_on_date(
    date: str | None = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Absences covering the given date (defaults to today). One entry per
    absent cadet — the inspection page crosses these out."""
    d = _parse_date(date) if date else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.query(CadetAbsence)
        .filter(and_(CadetAbsence.date_from <= d, CadetAbsence.date_to >= d))
        .all()
    )
    return [
        {
            "cin":       a.cadet_id,
            "date_from": a.date_from.date().isoformat(),
            "date_to":   a.date_to.date().isoformat(),
            "reason":    a.reason,
        }
        for a in rows
    ]


class InspectionMark(BaseModel):
    cin: int
    score: float | None = None
    absent: bool = False
    comments: list[dict] = []


class InspectionSubmit(BaseModel):
    date: str
    marks: list[InspectionMark]


@router.post("/inspections")
async def submit_inspection(
    body: InspectionSubmit,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Persist a submitted sheet and flag AWOLs — cadets marked absent with no
    matching absence log for the date."""
    d = _parse_date(body.date)
    absent_with_log = _absent_cins_on(db, d)

    awol = [m.cin for m in body.marks if m.absent and m.cin not in absent_with_log]

    sheet = InspectionSheet(
        date=d,
        submitted_by=idinfo.get("email"),
        submitted_at=datetime.now(),
        data={
            "marks": [
                {**m.model_dump(), "awol": m.cin in awol}
                for m in body.marks
            ],
        },
    )
    db.add(sheet)
    db.commit()

    return {"id": sheet.id, "awol": awol}
