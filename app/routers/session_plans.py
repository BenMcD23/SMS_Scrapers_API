"""NCO session plans — write, submit, and get staff approval.

Workflow: an NCO drafts a plan (the squadron's paper session plan, as a form)
→ submits it → staff are emailed → a staff member either approves it or sends it
back with per-section feedback → the NCO is emailed either way, fixes it, and
resubmits. Approved plans are locked.

Drafts are private to their author; once submitted, every NCO and staff member
can read the plan and leave comments on it. Only staff can approve or send back.
"""

import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.models import (
    SESSION_PLAN_SECTIONS, SessionPlan, SessionPlanAttachment,
    SessionPlanComment, User,
)

from core.config import SESSION_PLAN_ALERT_EMAIL
from core.db import get_db, get_or_create_user
from core.emailer import (
    send_email,
    session_plan_submitted_html,
    session_plan_amendments_html,
    session_plan_approved_html,
)
from core.security import get_user_role, require_staff, require_staff_or_nco
from form_generators.session_plan_pdf import build_session_plan_pdf

router = APIRouter()

ATTACHMENT_TYPES = ("image/png", "image/jpeg", "image/webp", "application/pdf")
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TIMETABLE_ROWS = 40

# Human labels for the section keys, used in the amendment email.
SECTION_LABELS = {
    "situation": "Situation",
    "mission": "Mission",
    "execution": "Execution",
    "any_questions": "Any Questions",
    "check_understanding": "Check Understanding",
}

# The NCO owns these while the plan is editable; staff never write them.
CONTENT_FIELDS = (
    "session_name", "session_ic", "aim", "participants", "rooming", "equipment",
    "notes", *SESSION_PLAN_SECTIONS,
)

# Statuses where the author may still edit and (re)submit.
EDITABLE_STATUSES = ("draft", "amendments_requested")


# ── request models ───────────────────────────────────────────────────────────

class TimetableRow(BaseModel):
    timing: str = ""
    event: str = ""
    location: str = ""


class SessionPlanBody(BaseModel):
    """Full plan content. Every field is optional so a half-finished draft can
    be saved; only the name is enforced, and only at submit time."""
    session_name: str = ""
    session_ic: str = ""
    session_date: str | None = None  # ISO date, "" / null when not set yet
    aim: str = ""
    participants: str = ""
    rooming: str = ""
    equipment: str = ""
    situation: str = ""
    mission: str = ""
    execution: str = ""
    any_questions: str = ""
    check_understanding: str = ""
    timetable: list[TimetableRow] = []
    notes: str = ""


class CommentBody(BaseModel):
    body: str = ""


class ReviewBody(BaseModel):
    """Staff decision. `feedback` is keyed by section; `note` is the overall
    message — required when sending a plan back, optional when approving."""
    note: str = ""
    feedback: dict[str, str] = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def _user_name(user: User | None) -> str:
    if not user:
        return "Unknown"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.email


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_date(value: str | None) -> datetime | None:
    """Accept the browser's "YYYY-MM-DD" (or a full ISO timestamp) — anything
    else is a client bug, so reject it rather than silently dropping the date."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session date")


def _is_staff(idinfo: dict) -> bool:
    return get_user_role(idinfo.get("email", "")) == "staff"


def _summary(plan: SessionPlan) -> dict:
    return {
        "id": plan.id,
        "session_name": plan.session_name,
        "session_ic": plan.session_ic or "",
        "session_date": _iso(plan.session_date),
        "status": plan.status,
        "author_name": _user_name(plan.author),
        "author_email": plan.author.email if plan.author else "",
        "created_at": _iso(plan.created_at),
        "updated_at": _iso(plan.updated_at),
        "submitted_at": _iso(plan.submitted_at),
    }


def _detail(plan: SessionPlan, viewer: User, is_staff: bool) -> dict:
    data = _summary(plan)
    is_author = plan.author_id == viewer.id
    data.update({
        "aim": plan.aim or "",
        "participants": plan.participants or "",
        "rooming": plan.rooming or "",
        "equipment": plan.equipment or "",
        **{key: getattr(plan, key) or "" for key in SESSION_PLAN_SECTIONS},
        "timetable": plan.timetable or [],
        "notes": plan.notes or "",
        "feedback": plan.feedback or {},
        "amendment_note": plan.amendment_note,
        "reviewed_at": _iso(plan.reviewed_at),
        "reviewed_by": plan.reviewed_by,
        "attachments": [
            {"id": a.id, "filename": a.filename, "mime_type": a.mime_type,
             "uploaded_at": _iso(a.uploaded_at)}
            for a in plan.attachments
        ],
        "comments": [
            {"id": c.id, "body": c.body, "author_name": _user_name(c.author),
             "author_email": c.author.email if c.author else "",
             "created_at": _iso(c.created_at),
             "can_delete": c.author_id == viewer.id or is_staff}
            for c in plan.comments
        ],
        "is_author": is_author,
        "can_review": is_staff and plan.status == "submitted",
        "can_edit": is_author and plan.status in EDITABLE_STATUSES,
    })
    return data


def _get_or_404(db: Session, plan_id: int, viewer: User) -> SessionPlan:
    """Fetch a plan the viewer is allowed to see: their own, or anyone's once
    it's out of draft. A plan they can't see is a 404 rather than a 403 —
    there's no reason to confirm it exists."""
    plan = db.query(SessionPlan).filter(SessionPlan.id == plan_id).first()
    if not plan or not (plan.author_id == viewer.id or plan.status != "draft"):
        raise HTTPException(status_code=404, detail="Session plan not found")
    return plan


def _apply_body(plan: SessionPlan, body: SessionPlanBody) -> None:
    for field in CONTENT_FIELDS:
        setattr(plan, field, (getattr(body, field) or "").strip())
    plan.session_date = _parse_date(body.session_date)
    if len(body.timetable) > MAX_TIMETABLE_ROWS:
        raise HTTPException(status_code=400, detail=f"At most {MAX_TIMETABLE_ROWS} timetable rows")
    plan.timetable = [
        {"timing": r.timing.strip(), "event": r.event.strip(), "location": r.location.strip()}
        for r in body.timetable
        if r.timing.strip() or r.event.strip() or r.location.strip()
    ]
    plan.updated_at = datetime.now()


def _date_str(plan: SessionPlan) -> str:
    return plan.session_date.strftime("%d/%m/%Y") if plan.session_date else ""


def _notify_staff_of_submission(plan: SessionPlan, resubmission: bool) -> None:
    if not SESSION_PLAN_ALERT_EMAIL:
        return
    verb = "resubmitted" if resubmission else "submitted"
    send_email(
        SESSION_PLAN_ALERT_EMAIL,
        f"Session plan {verb} for approval: {plan.session_name}",
        session_plan_submitted_html(
            plan.id, plan.session_name, plan.session_ic or "", _date_str(plan),
            _user_name(plan.author), resubmission,
        ),
    )


# ── endpoints ────────────────────────────────────────────────────────────────

@router.post("/session-plans")
async def create_plan(
    body: SessionPlanBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Create a plan as a draft — submitting it is a separate, explicit step."""
    user = get_or_create_user(db, idinfo)
    now = datetime.now()
    plan = SessionPlan(
        author_id=user.id, status="draft", feedback={}, timetable=[],
        created_at=now, updated_at=now, session_name="",
    )
    _apply_body(plan, body)
    # Default the Session I/C to whoever is writing it — nearly always true, and
    # still editable afterwards.
    if not plan.session_ic:
        plan.session_ic = _user_name(user)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _detail(plan, user, _is_staff(idinfo))


@router.get("/session-plans")
async def list_plans(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    # Everyone's plans, minus other people's unsubmitted drafts.
    plans = (
        db.query(SessionPlan)
        .filter(or_(SessionPlan.status != "draft", SessionPlan.author_id == user.id))
        .order_by(SessionPlan.updated_at.desc())
        .all()
    )
    return {"plans": [_summary(p) for p in plans], "is_staff": is_staff}


@router.get("/session-plans/{plan_id}")
async def get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    plan = _get_or_404(db, plan_id, user)
    return _detail(plan, user, is_staff)


@router.get("/session-plans/{plan_id}/pdf")
async def export_plan_pdf(
    plan_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """The plan as a printable PDF. Same visibility gate as viewing it, so an
    unsubmitted draft stays private to its author."""
    user = get_or_create_user(db, idinfo)
    plan = _get_or_404(db, plan_id, user)

    pdf = build_session_plan_pdf({
        "session_name": plan.session_name,
        "session_ic": plan.session_ic,
        "session_date": _date_str(plan),
        "aim": plan.aim,
        "participants": plan.participants,
        "rooming": plan.rooming,
        "equipment": plan.equipment,
        **{key: getattr(plan, key) or "" for key in SESSION_PLAN_SECTIONS},
        "timetable": plan.timetable or [],
        "notes": plan.notes,
        "author_name": _user_name(plan.author),
        "approved_by": plan.reviewed_by if plan.status == "approved" else None,
        "approved_at": plan.reviewed_at.strftime("%d/%m/%Y") if plan.reviewed_at else None,
    })

    safe_name = "".join(
        c for c in (plan.session_name or "session-plan") if c.isalnum() or c in " -_"
    ).strip() or "session-plan"
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


@router.put("/session-plans/{plan_id}")
async def update_plan(
    plan_id: int,
    body: SessionPlanBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can edit this plan")
    if plan.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="This plan is with staff for review and can't be edited"
            if plan.status == "submitted" else "This plan has been approved and is locked",
        )
    _apply_body(plan, body)
    db.commit()
    db.refresh(plan)
    return _detail(plan, user, is_staff)


@router.post("/session-plans/{plan_id}/submit")
async def submit_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Send the plan to staff for approval, emailing them. Also the resubmit
    path after amendments — the previous feedback is kept so the NCO (and the
    reviewer) can still see what was asked for."""
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can submit this plan")
    if plan.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="This plan has already been submitted"
            if plan.status == "submitted" else "This plan has already been approved",
        )
    missing = [
        label for field, label in (
            ("session_name", "Session name"),
            ("aim", "Aim/goal"),
            ("situation", "Situation"),
            ("mission", "Mission"),
            ("execution", "Execution"),
        )
        if not (getattr(plan, field) or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Fill in {', '.join(missing)} before submitting",
        )

    resubmission = plan.status == "amendments_requested"
    plan.status = "submitted"
    plan.submitted_at = datetime.now()
    plan.updated_at = plan.submitted_at
    db.commit()
    db.refresh(plan)

    _notify_staff_of_submission(plan, resubmission)
    return _detail(plan, user, is_staff)


@router.post("/session-plans/{plan_id}/approve")
async def approve_plan(
    plan_id: int,
    body: ReviewBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.status != "submitted":
        raise HTTPException(status_code=409, detail="This plan isn't awaiting review")

    plan.status = "approved"
    plan.feedback = {k: v.strip() for k, v in body.feedback.items()
                     if k in SESSION_PLAN_SECTIONS and v.strip()}
    plan.amendment_note = body.note.strip() or None
    plan.reviewed_at = datetime.now()
    plan.reviewed_by = idinfo.get("email", "")
    plan.updated_at = plan.reviewed_at
    db.commit()
    db.refresh(plan)

    if plan.author and plan.author.email:
        send_email(
            plan.author.email,
            f"Session plan approved: {plan.session_name}",
            session_plan_approved_html(
                plan.id, plan.session_name, plan.session_ic or "", _date_str(plan),
                _user_name(user), plan.amendment_note,
            ),
        )
    return _detail(plan, user, is_staff=True)


@router.post("/session-plans/{plan_id}/request-amendments")
async def request_amendments(
    plan_id: int,
    body: ReviewBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Send the plan back to the NCO with what needs changing."""
    user = get_or_create_user(db, idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.status != "submitted":
        raise HTTPException(status_code=409, detail="This plan isn't awaiting review")

    feedback = {k: v.strip() for k, v in body.feedback.items()
                if k in SESSION_PLAN_SECTIONS and v.strip()}
    note = body.note.strip()
    # Without either, the NCO gets an email telling them to change nothing.
    if not note and not feedback:
        raise HTTPException(
            status_code=400,
            detail="Say what needs changing — add a note or feedback on a section",
        )

    plan.status = "amendments_requested"
    plan.feedback = feedback
    plan.amendment_note = note or None
    plan.reviewed_at = datetime.now()
    plan.reviewed_by = idinfo.get("email", "")
    plan.updated_at = plan.reviewed_at
    db.commit()
    db.refresh(plan)

    if plan.author and plan.author.email:
        send_email(
            plan.author.email,
            f"Amendments needed on your session plan: {plan.session_name}",
            session_plan_amendments_html(
                plan.id, plan.session_name, plan.session_ic or "", _date_str(plan),
                _user_name(user), plan.amendment_note,
                [(SECTION_LABELS[k], feedback[k]) for k in SESSION_PLAN_SECTIONS if k in feedback],
            ),
        )
    return _detail(plan, user, is_staff=True)


@router.delete("/session-plans/{plan_id}")
async def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """The author can bin a plan that hasn't been approved yet."""
    user = get_or_create_user(db, idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can delete this plan")
    if plan.status == "approved":
        raise HTTPException(status_code=409, detail="Approved plans can't be deleted")
    db.delete(plan)
    db.commit()
    return {"ok": True}


# ── comments ─────────────────────────────────────────────────────────────────

@router.post("/session-plans/{plan_id}/comments")
async def add_comment(
    plan_id: int,
    body: CommentBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Anyone who can see the plan can leave a note on it — NCOs included. This
    is separate from the staff review, so commenting never changes the status."""
    user = get_or_create_user(db, idinfo)
    plan = _get_or_404(db, plan_id, user)
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write something first")
    db.add(SessionPlanComment(
        plan_id=plan.id, author_id=user.id, body=text[:5000], created_at=datetime.now(),
    ))
    db.commit()
    db.refresh(plan)
    return _detail(plan, user, _is_staff(idinfo))


@router.delete("/session-plans/{plan_id}/comments/{comment_id}")
async def delete_comment(
    plan_id: int,
    comment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    plan = _get_or_404(db, plan_id, user)
    comment = (
        db.query(SessionPlanComment)
        .filter(SessionPlanComment.id == comment_id,
                SessionPlanComment.plan_id == plan_id)
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.author_id != user.id and not is_staff:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")
    db.delete(comment)
    db.commit()
    db.refresh(plan)
    return _detail(plan, user, is_staff)


# ── attachments (the sheet's "Map (if applicable)" box) ──────────────────────

@router.post("/session-plans/{plan_id}/attachments")
async def upload_attachments(
    plan_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can add attachments")
    if plan.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="This plan can no longer be changed")

    now = datetime.now()
    for f in files:
        if f.content_type not in ATTACHMENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"{f.filename}: only PNG, JPEG, WebP or PDF files are accepted",
            )
        content = await f.read()
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=400, detail=f"{f.filename}: each file must be under 5 MB")
        db.add(SessionPlanAttachment(
            plan_id=plan.id, filename=f.filename or "attachment",
            mime_type=f.content_type, data=content, uploaded_at=now,
        ))
    plan.updated_at = now
    db.commit()
    db.refresh(plan)
    return _detail(plan, user, is_staff)


@router.get("/session-plans/{plan_id}/attachments/{attachment_id}")
async def get_attachment(
    plan_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    # Resolve the plan first so the same visibility gate covers the file.
    _get_or_404(db, plan_id, user)
    att = (
        db.query(SessionPlanAttachment)
        .filter(SessionPlanAttachment.id == attachment_id,
                SessionPlanAttachment.plan_id == plan_id)
        .first()
    )
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return StreamingResponse(
        io.BytesIO(att.data),
        media_type=att.mime_type,
        headers={"Content-Disposition": f'inline; filename="{att.filename}"'},
    )


@router.delete("/session-plans/{plan_id}/attachments/{attachment_id}")
async def delete_attachment(
    plan_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    plan = _get_or_404(db, plan_id, user)
    if plan.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can remove attachments")
    if plan.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="This plan can no longer be changed")
    att = (
        db.query(SessionPlanAttachment)
        .filter(SessionPlanAttachment.id == attachment_id,
                SessionPlanAttachment.plan_id == plan_id)
        .first()
    )
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    db.delete(att)
    plan.updated_at = datetime.now()
    db.commit()
    db.refresh(plan)
    return _detail(plan, user, is_staff)
