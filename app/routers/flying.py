"""Flying paperwork tracker: TG 21, TG 23 and AV Med per cadet on each AEF or
gliding event. The scraper creates and refreshes the events (see
scripts/flying_paperwork.py); staff tick the forms off and close events here."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from core.db import get_db
from core.security import require_staff
from database.models import FlyingEvent, FlyingPaperwork
from scripts.flying_paperwork import PAPERWORK_FIELDS, event_has_ticks

router = APIRouter()


def _row(p: FlyingPaperwork) -> dict:
    cadet = p.cadet
    return {
        "cin":        p.cadet_id,
        "first_name": cadet.first_name if cadet else None,
        "last_name":  cadet.last_name if cadet else None,
        "rank":       cadet.rank if cadet else None,
        "flight":     cadet.flight if cadet else None,
        "on_event":   p.on_event,
        "updated_at": p.updated_at,
        "updated_by": p.updated_by,
        **{field: getattr(p, field) for field in PAPERWORK_FIELDS},
    }


def _event(e: FlyingEvent) -> dict:
    return {
        "id":             e.id,
        "bader_event_id": e.bader_event_id,
        "title":          e.title,
        "category":       e.category,
        "date_from":      e.date_from,
        "date_to":        e.date_to,
        "on_bader":       e.on_bader,
        "last_seen_at":   e.last_seen_at,
        "has_ticks":      event_has_ticks(e),
        "cadets": sorted(
            (_row(p) for p in e.cadets),
            key=lambda r: ((r["last_name"] or "").lower(), (r["first_name"] or "").lower()),
        ),
    }


@router.get("/flying-paperwork")
def list_flying_events(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    events = (
        db.query(FlyingEvent)
        .options(selectinload(FlyingEvent.cadets).selectinload(FlyingPaperwork.cadet))
        .all()
    )
    # Soonest first; an event with no date sorts last rather than first.
    events.sort(key=lambda e: (e.date_from is None, e.date_from or datetime.max, e.title))
    return [_event(e) for e in events]


class PaperworkUpdate(BaseModel):
    """Any subset of the forms; anything left out keeps its current state."""
    tg21:   bool | None = None
    tg23:   bool | None = None
    av_med: bool | None = None


@router.patch("/flying-paperwork/{event_id}/cadets/{cin}")
def update_paperwork(
    event_id: int,
    cin: int,
    body: PaperworkUpdate,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    row = (
        db.query(FlyingPaperwork)
        .filter(FlyingPaperwork.flying_event_id == event_id, FlyingPaperwork.cadet_id == cin)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="That cadet isn't on this flying event")
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")
    for field, value in changes.items():
        setattr(row, field, value)
    row.updated_at = datetime.now()
    row.updated_by = idinfo.get("email")
    db.commit()
    db.refresh(row)
    return _row(row)


@router.delete("/flying-paperwork/{event_id}")
def close_flying_event(
    event_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Close an event and delete its paperwork record. If it's still on Bader
    the next scrape will start tracking it again from scratch."""
    event = db.query(FlyingEvent).filter(FlyingEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Flying event not found")
    db.delete(event)
    db.commit()
    return {"ok": True}
