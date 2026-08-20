"""Event data scraped from Bader, plus JI/AO document generation."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from database.models import AllEvent, Cadet, CadetEvent, Event317

from scripts.ji_ao_ai import generate_ao_description_ai, generate_ji_description_ai
from scripts.ji_ao_generator import ao_fields, generate_ao, generate_ji, ji_fields

from core.db import get_db
from core.security import require_staff

router = APIRouter()


@router.get("/events")
def get_events(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return db.query(Event317).all()


@router.get("/cadet-events")
def get_cadet_events(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    # Eager-load the full tree in a handful of queries — lazy loading here was
    # one query per event, per sub-app, and per attending cadet.
    parent_events = (
        db.query(AllEvent)
        .filter(AllEvent.parent_id == None)
        .options(
            selectinload(AllEvent.cadet_events).selectinload(CadetEvent.cadet),
            selectinload(AllEvent.sub_apps)
            .selectinload(AllEvent.cadet_events)
            .selectinload(CadetEvent.cadet),
        )
        .all()
    )

    def cadet_list(event):
        return [
            {
                "cin":        ce.cadet.cin,
                "first_name": ce.cadet.first_name,
                "last_name":  ce.cadet.last_name,
                "rank":       ce.cadet.rank,
                "flight":     ce.cadet.flight,
            }
            for ce in event.cadet_events
            if ce.cadet
        ]

    return [
        {
            "id":          e.id,
            "title":       e.title,
            "cadet_count": len(e.cadet_events),
            "cadets":      cadet_list(e),
            "sub_apps": [
                {
                    "id":          s.id,
                    "title":       s.title,
                    "cadet_count": len(s.cadet_events),
                    "cadets":      cadet_list(s),
                }
                for s in e.sub_apps
            ],
        }
        for e in parent_events
    ]


@router.get("/bans")
def get_bans(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    banned = (
        db.query(Cadet)
        .filter(Cadet.banned == True)
        .options(selectinload(Cadet.cadet_events).selectinload(CadetEvent.event))
        .all()
    )
    return [
        {
            "cin":        c.cin,
            "first_name": c.first_name,
            "last_name":  c.last_name,
            "rank":       c.rank,
            "events": [
                {"event_id": ce.event_id, "event_title": ce.event.title if ce.event else f"Event {ce.event_id}"}
                for ce in c.cadet_events
                if ce.event
            ],
        }
        for c in banned
    ]


class DocFields(BaseModel):
    """Edited section values from the generator UI. Anything absent falls back
    to what the generator computes from the event, so a partial body is fine."""
    fields: dict[str, str] = {}


def _get_event(db: Session, event_id: int) -> Event317:
    event = db.query(Event317).filter(Event317.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/generate-doc/{event_id}/fields")
def get_doc_fields(
    event_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Default text for every editable section of both documents — what the UI
    fills its preview with before the user changes anything."""
    event = _get_event(db, event_id)
    return {"ji": ji_fields(event), "ao": ao_fields(event)}


@router.post("/generate-doc/{event_id}/{action}/ai-description")
def generate_ai_description(
    event_id: int,
    action: str,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Write just the description section with AI and hand it back as text, so
    it lands in the editable box for review rather than straight into the file."""
    event = _get_event(db, event_id)
    if action not in ("ji", "ao"):
        raise HTTPException(status_code=400, detail="Invalid action")
    try:
        description = (
            generate_ji_description_ai(event) if action == "ji"
            else generate_ao_description_ai(event)
        )
    except Exception as e:
        print(f"Error generating AI description: {e}")
        raise HTTPException(status_code=502, detail="AI generation failed — try again or write it yourself")
    return {"description": description}


@router.post("/generate-doc/{event_id}/{action}")
def generate_doc_endpoint(
    event_id: int,
    action: str,
    data: DocFields | None = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    event = _get_event(db, event_id)
    fields = data.fields if data else {}

    try:
        if action == "ji":
            file_buffer = generate_ji(event, fields=fields)
            filename = f"JI_{event.reference}.docx"
        elif action == "ao":
            file_buffer = generate_ao(event, fields=fields)
            filename = f"AO_{event.reference}.docx"
        else:
            raise HTTPException(status_code=400, detail="Invalid action")

        safe_filename = filename.replace('"', '').replace('\n', '').replace('\r', '')
        return StreamingResponse(
            file_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error generating document: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate document")
