"""Assessment and inspection sheets saved on the server while they are being
filled in, so a reload, a cleared cache or a different phone carries on where
the assessor left off. Each person has one draft per kind of sheet; nobody else
reads it, and it counts for nothing until the sheet itself is submitted."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.db import get_db
from core.security import require_staff, require_staff_or_nco, require_staff_or_snco
from database.models import AssessmentDraft

router = APIRouter()

# Who may keep a draft of each sheet: the same people its submit endpoint lets
# in (inspections.py and assessments.py), so a draft can't be started by
# someone who could never send it.
ACCESS = {
    "inspection": require_staff_or_snco,
    "leadership": require_staff_or_nco,
    "radio":      require_staff_or_nco,
    "space":      require_staff_or_nco,
    "moi":        require_staff,
}

# Generous for a form, small for a database row. Space and MOI keep the
# cadet's drawn signature in the form, which is the bulk of it.
MAX_DRAFT_BYTES = 1_000_000


def _access(kind: str, authorization: str = Header(None)) -> dict:
    check = ACCESS.get(kind)
    if check is None:
        raise HTTPException(status_code=404, detail="No such assessment sheet")
    return check(authorization)


def _owner(idinfo: dict) -> str:
    owner = (idinfo.get("email") or "").strip().lower()
    if not owner:
        raise HTTPException(status_code=400, detail="No email on this sign-in")
    return owner


def _find(db: Session, owner: str, kind: str) -> AssessmentDraft | None:
    return (
        db.query(AssessmentDraft)
        .filter(AssessmentDraft.owner == owner, AssessmentDraft.kind == kind)
        .first()
    )


def _out(draft: AssessmentDraft) -> dict:
    return {"data": draft.data, "saved_at": draft.saved_at, "updated_at": draft.updated_at.isoformat()}


class DraftBody(BaseModel):
    data: dict
    # The page's clock when it saved, so it can tell which of its own copy and
    # this one is newer.
    saved_at: str | None = None


@router.get("/assessment-drafts/{kind}")
def get_draft(kind: str, db: Session = Depends(get_db), idinfo: dict = Depends(_access)):
    """The caller's sheet in progress, or 404 when they have none."""
    draft = _find(db, _owner(idinfo), kind)
    if not draft:
        raise HTTPException(status_code=404, detail="No draft saved")
    return _out(draft)


@router.put("/assessment-drafts/{kind}")
def save_draft(kind: str, body: DraftBody, db: Session = Depends(get_db),
               idinfo: dict = Depends(_access)):
    """Replace the caller's sheet in progress with the one sent. The page sends
    the whole sheet every time, so a save that failed is made good by the next."""
    if len(json.dumps(body.data)) > MAX_DRAFT_BYTES:
        raise HTTPException(status_code=413, detail="Draft too large to save")
    owner = _owner(idinfo)
    draft = _find(db, owner, kind)
    if draft:
        draft.data = body.data  # reassign so SQLAlchemy sees the JSON change
        draft.saved_at = body.saved_at
        draft.updated_at = datetime.now()
    else:
        draft = AssessmentDraft(owner=owner, kind=kind, data=body.data,
                                saved_at=body.saved_at, updated_at=datetime.now())
        db.add(draft)
    db.commit()
    return _out(draft)


@router.delete("/assessment-drafts/{kind}")
def delete_draft(kind: str, db: Session = Depends(get_db), idinfo: dict = Depends(_access)):
    """Throw away the caller's sheet in progress (cleared, or submitted)."""
    deleted = (
        db.query(AssessmentDraft)
        .filter(AssessmentDraft.owner == _owner(idinfo), AssessmentDraft.kind == kind)
        .delete()
    )
    db.commit()
    return {"deleted": bool(deleted)}
