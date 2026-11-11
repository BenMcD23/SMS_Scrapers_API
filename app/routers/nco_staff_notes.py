"""NCO staff notes — what staff notice about their cadet NCOs, kept for appraisals.

On a parade night staff tap an NCO, say or type what they saw, and move on. Or,
at the end of the night, talk through several NCOs at once and let the AI split
it into a note each, checked before it saves. Over a review period the notes
build each NCO's profile, and the next appraisal is drafted from the ones it
hasn't used yet (see routers/nco_appraisals.py).

Staff-only throughout, NCOs included in "who can't see it": these are staff's
views of the NCOs themselves. The NCO comments page (routers/nco_comments.py)
is the shared one.

Any staff member can read every note. Only the author can edit or delete one —
or anyone, once the author's account has gone — and a note an appraisal has
used can't be edited, because the appraisal was written from what it said.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from core import transcribe as speech
from core.db import get_db, get_or_create_user
from core.llm import model_label
from core.ranks import nco_team
from core.security import require_staff
from database.models import NCO_NOTE_SOURCES, Cadet, NcoAppraisal, NcoStaffNote, User
from routers.nco_appraisals import cadet_name, is_nco

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_NOTE_CHARS = 2000
MAX_BATCH = 30
MAX_SPLIT_CHARS = 8000
# A minute of Opus is well under 1MB; this allows several minutes of the
# bulkiest browser format without letting a stray video through.
MAX_AUDIO_BYTES = 10 * 1024 * 1024
RECENT_COUNT = 15


# ── request models ───────────────────────────────────────────────────────────

class NoteBody(BaseModel):
    cadet_cin: int
    body: str
    note_date: str | None = None  # "YYYY-MM-DD"; today when omitted
    source: str = "typed"


class BatchItem(BaseModel):
    cadet_cin: int
    body: str


class BatchBody(BaseModel):
    notes: list[BatchItem] = Field(min_length=1, max_length=MAX_BATCH)
    note_date: str | None = None
    source: str = "voice"


class EditBody(BaseModel):
    body: str
    # A misheard name is the likeliest mistake, so a note can be moved.
    cadet_cin: int | None = None


class SplitBody(BaseModel):
    text: str


# ── helpers ──────────────────────────────────────────────────────────────────

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _user_name(user: User | None) -> str:
    if not user:
        return "Former staff"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email


def _parse_date(value: str | None) -> datetime:
    """A note is filed against a parade night, not a moment: midnight that day."""
    if not value:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    return parsed.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def _clean_body(text: str) -> str:
    body = (text or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Write or say something first")
    return body[:MAX_NOTE_CHARS]


def _check_source(source: str) -> str:
    if source not in NCO_NOTE_SOURCES:
        raise HTTPException(status_code=400, detail=f"Source must be one of {', '.join(NCO_NOTE_SOURCES)}")
    return source


def _nco_or_error(db: Session, cin: int) -> Cadet:
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"No cadet with CIN {cin}")
    if not is_nco(cadet):
        raise HTTPException(
            status_code=400,
            detail=f"{cadet.first_name} {cadet.last_name} isn't in the NCO team",
        )
    return cadet


def _can_change(note: NcoStaffNote, user: User) -> bool:
    return note.author_id is None or note.author_id == user.id


def _to_dict(note: NcoStaffNote, viewer: User) -> dict:
    appraisal = note.appraisal
    return {
        "id": note.id,
        "cadet_cin": note.cadet_id,
        # The live name where the cadet's still here, so a promotion shows; the
        # snapshot once they've gone.
        "cadet_name": cadet_name(note.cadet) if note.cadet else note.cadet_name,
        "body": note.body,
        "note_date": _iso(note.note_date),
        "source": note.source,
        "author_name": _user_name(note.author),
        "is_mine": note.author_id == viewer.id,
        "can_edit": _can_change(note, viewer) and note.appraisal_id is None,
        "can_delete": _can_change(note, viewer),
        "appraisal_id": note.appraisal_id,
        "appraisal_date": _iso(appraisal.appraisal_date) if appraisal else None,
        "created_at": _iso(note.created_at),
        "updated_at": _iso(note.updated_at),
    }


def _query(db: Session):
    return db.query(NcoStaffNote).options(
        joinedload(NcoStaffNote.author),
        joinedload(NcoStaffNote.cadet),
        joinedload(NcoStaffNote.appraisal),
    ).order_by(NcoStaffNote.note_date.desc(), NcoStaffNote.created_at.desc(), NcoStaffNote.id.desc())


def _get_or_404(db: Session, note_id: int) -> NcoStaffNote:
    note = _query(db).filter(NcoStaffNote.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


def _new_note(cadet: Cadet, user: User, body: str, when: datetime, source: str) -> NcoStaffNote:
    return NcoStaffNote(
        cadet_id=cadet.cin, cadet_name=cadet_name(cadet), author_id=user.id,
        body=body, note_date=when, source=source, created_at=datetime.now(),
    )


# ── overview and profiles ────────────────────────────────────────────────────

@router.get("/nco-notes")
def overview(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The NCO team with how many notes each has waiting for their next
    appraisal, plus the latest notes across everyone."""
    user = get_or_create_user(db, idinfo)
    ncos = nco_team(db)

    open_counts = dict(
        db.query(NcoStaffNote.cadet_id, func.count(NcoStaffNote.id))
        .filter(NcoStaffNote.appraisal_id.is_(None), NcoStaffNote.cadet_id.isnot(None))
        .group_by(NcoStaffNote.cadet_id).all()
    )
    last_noted = dict(
        db.query(NcoStaffNote.cadet_id, func.max(NcoStaffNote.note_date))
        .filter(NcoStaffNote.cadet_id.isnot(None))
        .group_by(NcoStaffNote.cadet_id).all()
    )
    last_appraised = dict(
        db.query(NcoAppraisal.cadet_id, func.max(NcoAppraisal.appraisal_date))
        .group_by(NcoAppraisal.cadet_id).all()
    )

    return {
        "ncos": [
            {
                "cin": c.cin,
                "name": cadet_name(c),
                "rank": c.rank,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "flight": c.flight,
                "open_notes": open_counts.get(c.cin, 0),
                "last_note_date": _iso(last_noted.get(c.cin)),
                "last_appraisal_date": _iso(last_appraised.get(c.cin)),
            }
            for c in ncos
        ],
        "recent": [_to_dict(n, user) for n in _query(db).limit(RECENT_COUNT).all()],
        "voice_available": speech.available(),
    }


@router.get("/nco-notes/ncos/{cin}")
def profile(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """One NCO's notes, newest first, with their appraisals for context. The
    notes no appraisal has used yet are what the next one will be drafted from."""
    user = get_or_create_user(db, idinfo)
    cadet = _nco_or_error(db, cin)
    notes = _query(db).filter(NcoStaffNote.cadet_id == cin).all()
    appraisals = (
        db.query(NcoAppraisal).filter(NcoAppraisal.cadet_id == cin)
        .order_by(NcoAppraisal.appraisal_date.desc()).all()
    )
    last = appraisals[0] if appraisals else None
    return {
        "nco": {
            "cin": cadet.cin,
            "name": cadet_name(cadet),
            "rank": cadet.rank,
            "first_name": cadet.first_name,
            "last_name": cadet.last_name,
            "flight": cadet.flight,
        },
        "notes": [_to_dict(n, user) for n in notes],
        "open_notes": sum(1 for n in notes if n.appraisal_id is None),
        "appraisals": [
            {"id": a.id, "appraisal_date": _iso(a.appraisal_date),
             "next_review_date": _iso(a.next_review_date)}
            for a in appraisals
        ],
        "next_review_date": _iso(last.next_review_date) if last else None,
    }


# ── writing notes ────────────────────────────────────────────────────────────

@router.post("/nco-notes", status_code=201)
def create_note(
    body: NoteBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)
    text = _clean_body(body.body)
    source = _check_source(body.source)
    when = _parse_date(body.note_date)
    cadet = _nco_or_error(db, body.cadet_cin)

    note = _new_note(cadet, user, text, when, source)
    db.add(note)
    db.commit()
    return _to_dict(_get_or_404(db, note.id), user)


@router.post("/nco-notes/batch", status_code=201)
def create_notes(
    body: BatchBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Save the reviewed split of a several-NCO debrief. All or nothing: every
    note is checked before any is written, so a bad row doesn't leave half the
    night saved and the rest to re-enter."""
    user = get_or_create_user(db, idinfo)
    source = _check_source(body.source)
    when = _parse_date(body.note_date)
    rows = [(_nco_or_error(db, item.cadet_cin), _clean_body(item.body)) for item in body.notes]

    notes = [_new_note(cadet, user, text, when, source) for cadet, text in rows]
    db.add_all(notes)
    db.commit()
    ids = [n.id for n in notes]
    saved = {n.id: n for n in _query(db).filter(NcoStaffNote.id.in_(ids)).all()}
    return {"notes": [_to_dict(saved[i], user) for i in ids]}


@router.put("/nco-notes/{note_id}")
def edit_note(
    note_id: int,
    body: EditBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)
    note = _get_or_404(db, note_id)
    if not _can_change(note, user):
        raise HTTPException(status_code=403, detail="You can only edit your own notes")
    if note.appraisal_id is not None:
        raise HTTPException(
            status_code=409,
            detail="An appraisal has been written from this note, so it can't be changed",
        )
    note.body = _clean_body(body.body)
    if body.cadet_cin is not None and body.cadet_cin != note.cadet_id:
        cadet = _nco_or_error(db, body.cadet_cin)
        note.cadet_id, note.cadet_name = cadet.cin, cadet_name(cadet)
    note.updated_at = datetime.now()
    db.commit()
    return _to_dict(_get_or_404(db, note_id), user)


@router.delete("/nco-notes/{note_id}")
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Allowed even once an appraisal has used it: removing something that was
    wrong is always fine, and the appraisal keeps its own text."""
    user = get_or_create_user(db, idinfo)
    note = _get_or_404(db, note_id)
    if not _can_change(note, user):
        raise HTTPException(status_code=403, detail="You can only delete your own notes")
    db.delete(note)
    db.commit()
    return {"ok": True}


# ── voice and the several-NCO split ──────────────────────────────────────────

@router.post("/nco-notes/transcribe")
def transcribe_note(  # sync on purpose — the slow call runs in the threadpool
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """A recorded clip as text, for staff to check before saving. The audio is
    read into memory, sent on and dropped; it's never written anywhere."""
    if not speech.is_supported(audio.content_type):
        raise HTTPException(status_code=415, detail="That recording format isn't supported")
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too long — keep notes under a few minutes")
    if not data:
        raise HTTPException(status_code=400, detail="The recording was empty")

    names = [cadet_name(c) for c in nco_team(db)]
    try:
        text, model = speech.transcribe(data, audio.content_type, names)
    except Exception as e:
        logger.error(f"transcription failed: {e}")
        raise HTTPException(
            status_code=502,
            detail="Couldn't turn that recording into text. Try again, or type the note.",
        )
    if not text:
        raise HTTPException(status_code=422, detail="Didn't catch anything — try again a little closer")
    return {"text": text, "model": model, "model_label": speech.MODEL_LABELS.get(model, model)}


@router.post("/nco-notes/split")
def split_debrief(
    body: SplitBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """One debrief about several NCOs, split into a suggested note each. Nothing
    is saved: the page shows the split for staff to correct, then posts it to
    /nco-notes/batch."""
    from scripts.nco_notes_ai import split_notes

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write or say something first")
    roster = [{"cin": c.cin, "name": cadet_name(c)} for c in nco_team(db)]
    if not roster:
        raise HTTPException(status_code=409, detail="There are no NCOs on the roster to file notes against")

    try:
        notes, unassigned, model = split_notes(text[:MAX_SPLIT_CHARS], roster)
    except Exception as e:
        logger.error(f"splitting notes failed: {e}")
        raise HTTPException(
            status_code=502,
            detail="The AI couldn't sort those notes. Try again, or add them one NCO at a time.",
        )

    names = {r["cin"]: r["name"] for r in roster}
    return {
        "notes": [{**n, "cadet_name": names[n["cin"]]} for n in notes],
        "unassigned": unassigned,
        "model": model,
        "model_label": model_label(model),
    }
