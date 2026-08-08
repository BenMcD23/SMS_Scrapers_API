"""NCO quick comments — short notes about a cadet or about the squadron.

An NCO writes a one-line-ish observation (dated today by default), optionally
pinned to a cadet, and anyone else who can see the page can reply on the chain.
It's the quick stuff that otherwise only gets said out loud and forgotten —
deliberately lighter than an assessment sheet.

Access matches the assessment sheets: every NCO, SNCO and staff member can read
and write everything. A comment can be deleted by whoever wrote it, or by staff.
Cadet-linked comments snapshot the cadet's name, so a note still reads correctly
after that cadet leaves and the scraper removes their record.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload, selectinload

from database.models import Cadet, NcoComment, NcoCommentReply, User

from core.db import get_db, get_or_create_user
from core.security import get_user_role, require_staff_or_nco

router = APIRouter()

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 5000


class CommentBody(BaseModel):
    subject: str = ""
    body: str = ""
    comment_date: str | None = None  # "YYYY-MM-DD"; today when omitted
    cadet_cin: int | None = None     # null = a general comment


class ReplyBody(BaseModel):
    body: str = ""


# ── helpers ──────────────────────────────────────────────────────────────────

def _user_name(user: User | None) -> str:
    if not user:
        return "Unknown"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.email


def _cadet_name(cadet: Cadet) -> str:
    name = f"{cadet.first_name or ''} {cadet.last_name or ''}".strip()
    return f"{cadet.rank} {name}".strip() if cadet.rank else name


def _is_staff(idinfo: dict) -> bool:
    return get_user_role(idinfo["email"]) == "staff"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_date(value: str | None) -> datetime:
    """The browser's "YYYY-MM-DD" (or a full ISO timestamp), normalised to
    midnight — a comment is filed against a day, not a moment."""
    if not value:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    return parsed.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def _to_dict(comment: NcoComment, viewer: User, is_staff: bool) -> dict:
    return {
        "id": comment.id,
        "subject": comment.subject,
        "body": comment.body,
        "comment_date": _iso(comment.comment_date),
        # The live cadet where there still is one, so a rename doesn't strand the
        # note on an old spelling; the snapshot once they've left.
        "cadet_cin": comment.cadet_id,
        "cadet_name": _cadet_name(comment.cadet) if comment.cadet else comment.cadet_name,
        "cadet_flight": comment.cadet.flight if comment.cadet else None,
        "author_name": _user_name(comment.author),
        "author_email": comment.author.email if comment.author else "",
        "created_at": _iso(comment.created_at),
        "is_mine": comment.author_id == viewer.id,
        "can_delete": comment.author_id == viewer.id or is_staff,
        "replies": [
            {
                "id": r.id,
                "body": r.body,
                "author_name": _user_name(r.author),
                "author_email": r.author.email if r.author else "",
                "created_at": _iso(r.created_at),
                "can_delete": r.author_id == viewer.id or is_staff,
            }
            for r in comment.replies
        ],
    }


def _all_comments(db: Session) -> list[NcoComment]:
    return (
        db.query(NcoComment)
        .options(
            joinedload(NcoComment.author),
            joinedload(NcoComment.cadet),
            selectinload(NcoComment.replies).joinedload(NcoCommentReply.author),
        )
        .order_by(NcoComment.comment_date.desc(), NcoComment.created_at.desc())
        .all()
    )


def _list_response(db: Session, viewer: User, is_staff: bool) -> dict:
    return {
        "comments": [_to_dict(c, viewer, is_staff) for c in _all_comments(db)],
        "is_staff": is_staff,
    }


def _get_or_404(db: Session, comment_id: int) -> NcoComment:
    comment = db.query(NcoComment).filter(NcoComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


# ── comments ─────────────────────────────────────────────────────────────────

@router.get("/nco-comments")
def list_comments(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    return _list_response(db, user, _is_staff(idinfo))


@router.post("/nco-comments", status_code=201)
def create_comment(
    body: CommentBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    subject = body.subject.strip()
    text = body.body.strip()
    if not subject:
        raise HTTPException(status_code=400, detail="Give the comment a subject")
    if not text:
        raise HTTPException(status_code=400, detail="Write something first")

    cadet = None
    if body.cadet_cin is not None:
        cadet = db.query(Cadet).filter(Cadet.cin == body.cadet_cin).first()
        if not cadet:
            raise HTTPException(status_code=404, detail="Cadet not found")

    comment = NcoComment(
        author_id=user.id,
        subject=subject[:MAX_SUBJECT_CHARS],
        body=text[:MAX_BODY_CHARS],
        comment_date=_parse_date(body.comment_date),
        cadet_id=cadet.cin if cadet else None,
        cadet_name=_cadet_name(cadet) if cadet else "",
        created_at=datetime.now(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _to_dict(comment, user, _is_staff(idinfo))


@router.delete("/nco-comments/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    comment = _get_or_404(db, comment_id)
    if comment.author_id != user.id and not is_staff:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")
    db.delete(comment)
    db.commit()
    return {"ok": True}


# ── replies ──────────────────────────────────────────────────────────────────

@router.post("/nco-comments/{comment_id}/replies", status_code=201)
def add_reply(
    comment_id: int,
    body: ReplyBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    comment = _get_or_404(db, comment_id)
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write something first")
    db.add(NcoCommentReply(
        comment_id=comment.id, author_id=user.id,
        body=text[:MAX_BODY_CHARS], created_at=datetime.now(),
    ))
    db.commit()
    db.refresh(comment)
    return _to_dict(comment, user, _is_staff(idinfo))


@router.delete("/nco-comments/{comment_id}/replies/{reply_id}")
def delete_reply(
    comment_id: int,
    reply_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    comment = _get_or_404(db, comment_id)
    reply = (
        db.query(NcoCommentReply)
        .filter(NcoCommentReply.id == reply_id, NcoCommentReply.comment_id == comment_id)
        .first()
    )
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")
    if reply.author_id != user.id and not is_staff:
        raise HTTPException(status_code=403, detail="You can only delete your own replies")
    db.delete(reply)
    db.commit()
    db.refresh(comment)
    return _to_dict(comment, user, is_staff)
