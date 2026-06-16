"""Event data scraped from Bader, plus JI/AO document generation."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database.models import AllEvent, Cadet, Event317

from scripts.ji_ao_generator import generate_ji, generate_ao

from core.db import get_db
from core.security import require_staff

router = APIRouter()


@router.get("/events")
async def get_events(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return db.query(Event317).all()


@router.get("/cadet-events")
async def get_cadet_events(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    parent_events = db.query(AllEvent).filter(AllEvent.parent_id == None).all()

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
async def get_bans(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    banned = db.query(Cadet).filter(Cadet.banned == True).all()
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


@router.get("/generate-doc/{event_id}/{action}")
async def generate_doc_endpoint(
    event_id: int,
    action: str,
    ai: bool = False,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    event = db.query(Event317).filter(Event317.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        if action == "ji":
            file_buffer = generate_ji(event, use_ai=ai)
            filename = f"JI_{event.reference}.docx"
        elif action == "ao":
            file_buffer = generate_ao(event, use_ai=ai)
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
        detail = "AI generation failed — try again or generate without AI" if ai else "Failed to generate document"
        raise HTTPException(status_code=502 if ai else 500, detail=detail)
