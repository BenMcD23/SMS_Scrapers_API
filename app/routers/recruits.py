"""New recruits' joining forms (RAFAC 3822A consent + 3822H health declaration).

Staff add a recruit on SMS and get a link to send the parent. The parent fills
the form in at home on the squadron website, saving as they go, then signs and
submits. Staff watch the progress on SMS and, once it's in, read the answers
and download the filled official PDFs.

Two halves:
  /recruits/...         staff only, behind the usual Google sign-in
  /recruit-form/{token} public; the token in the link is the only credential

The public half answers every bad link — unknown, expired, deleted — with the
same 404, so a link can't be probed for whether it ever existed. It never sends
answers back once the form is submitted.
"""

import base64
import binascii
import re
import secrets
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.orm import Session

from core import recruit_form
from core.config import RECRUIT_FORM_URL
from core.db import get_db
from core.http import content_disposition
from core.recruit_form import RecruitFormData
from core.security import require_staff
from database.models import RecruitApplication
from form_generators.recruit_pdf import build_pdf

router = APIRouter()

LINK_DAYS = 14
BAD_LINK = "This link is invalid or has expired. Ask the squadron for a new one."
MAX_SIGNATURE_BYTES = 300_000
_PNG = b"\x89PNG\r\n\x1a\n"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


# ── helpers ──────────────────────────────────────────────────────────────────

def _link(app: RecruitApplication) -> str:
    return f"{RECRUIT_FORM_URL}/{app.token}"


def _status(app: RecruitApplication) -> str:
    if app.submitted_at:
        return "submitted"
    if app.last_saved_at:
        return "in_progress"
    return "opened" if app.opened_at else "not_started"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _png_data_url(png: bytes | None) -> str | None:
    return f"data:image/png;base64,{base64.b64encode(png).decode()}" if png else None


def _summary(app: RecruitApplication, now: datetime) -> dict:
    form = recruit_form.parse(app.data)
    return {
        "id": app.id,
        "cadetFirstName": app.cadet_first_name,
        "cadetLastName": app.cadet_last_name,
        "parentName": app.parent_name,
        "parentEmail": app.parent_email,
        "status": _status(app),
        "progress": recruit_form.progress(form, bool(app.submitted_at)),
        "link": _link(app),
        "expiresAt": app.expires_at.isoformat(),
        "expired": not app.submitted_at and app.expires_at <= now,
        "createdAt": app.created_at.isoformat(),
        "createdBy": app.created_by,
        "openedAt": _iso(app.opened_at),
        "lastSavedAt": _iso(app.last_saved_at),
        "submittedAt": _iso(app.submitted_at),
    }


def _get(db: Session, recruit_id: int) -> RecruitApplication:
    app = db.get(RecruitApplication, recruit_id)
    if not app:
        raise HTTPException(status_code=404, detail="Recruit not found.")
    return app


def _by_token(db: Session, token: str) -> RecruitApplication:
    app = db.query(RecruitApplication).filter(RecruitApplication.token == token).first()
    # Submitted forms stay reachable past expiry so the parent still sees
    # "already sent" rather than a scary dead link.
    if not app or (not app.submitted_at and app.expires_at <= datetime.now()):
        raise HTTPException(status_code=404, detail=BAD_LINK)
    return app


def _open_for_editing(db: Session, token: str) -> RecruitApplication:
    app = _by_token(db, token)
    if app.submitted_at:
        raise HTTPException(status_code=409, detail="This form has already been submitted.")
    return app


def _decode_signature(data_url: str | None, who: str) -> bytes:
    """A PNG from the signature pad's data URL. Anything else is refused: it is
    drawn straight into the PDF."""
    prefix = "data:image/png;base64,"
    if not data_url or not data_url.startswith(prefix):
        raise HTTPException(status_code=422, detail=f"The {who} signature is missing.")
    try:
        png = base64.b64decode(data_url[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail=f"The {who} signature couldn't be read.")
    if not png.startswith(_PNG) or len(png) > MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=422, detail=f"The {who} signature couldn't be read.")
    return png


def _prefill(cadet_name: str, parent_name: str, parent_email: str) -> dict:
    """What staff already know, so the parent doesn't type it twice."""
    forename, _, surname = parent_name.partition(" ")
    return RecruitFormData.model_validate({
        "cadet": {"fullName": cadet_name},
        "nok": {"fullName": parent_name, "email": parent_email},
        "signer": {"forename": forename, "surname": surname},
    }).model_dump(mode="json")


# ── staff ────────────────────────────────────────────────────────────────────

class NewRecruit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadetFirstName: Name
    cadetLastName: Name
    parentName: Name
    parentEmail: Name


@router.get("/recruits")
def list_recruits(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    now = datetime.now()
    apps = db.query(RecruitApplication).order_by(RecruitApplication.created_at.desc()).all()
    return [_summary(a, now) for a in apps]


@router.post("/recruits", status_code=201)
def add_recruit(body: NewRecruit, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    if not _EMAIL.match(body.parentEmail):
        raise HTTPException(status_code=422, detail="Enter the parent's email address, like name@example.com.")
    now = datetime.now()
    app = RecruitApplication(
        cadet_first_name=body.cadetFirstName,
        cadet_last_name=body.cadetLastName,
        parent_name=body.parentName,
        parent_email=body.parentEmail,
        token=secrets.token_urlsafe(32),
        expires_at=now + timedelta(days=LINK_DAYS),
        data=_prefill(f"{body.cadetFirstName} {body.cadetLastName}", body.parentName, body.parentEmail),
        created_by=idinfo.get("email"),
        created_at=now,
    )
    db.add(app)
    db.commit()
    return _summary(app, now)


@router.get("/recruits/{recruit_id}")
def get_recruit(recruit_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    app = _get(db, recruit_id)
    form = recruit_form.parse(app.data)
    return {
        **_summary(app, datetime.now()),
        "data": form.model_dump(mode="json"),
        "missing": recruit_form.missing(form),
        "options": recruit_form.options(),
        "parentSignature": _png_data_url(app.parent_signature),
        "cadetSignature": _png_data_url(app.cadet_signature),
        "hasHealthDeclaration": bool(form.medical.has),
    }


@router.post("/recruits/{recruit_id}/extend")
def extend_link(recruit_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Give the link another LINK_DAYS from now (works on an expired link too)."""
    app = _get(db, recruit_id)
    if app.submitted_at:
        raise HTTPException(status_code=409, detail="This form has already been submitted.")
    now = datetime.now()
    app.expires_at = now + timedelta(days=LINK_DAYS)
    db.commit()
    return _summary(app, now)


@router.delete("/recruits/{recruit_id}", status_code=204)
def delete_recruit(recruit_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    db.delete(_get(db, recruit_id))
    db.commit()


@router.get("/recruits/{recruit_id}/pdf")
def recruit_pdf(
    recruit_id: int,
    form: Literal["both", "3822A", "3822H"] = "both",
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The filled official form(s), drawn on demand so no copy of the medical
    answers sits around as a file."""
    app = _get(db, recruit_id)
    if not app.submitted_at:
        raise HTTPException(status_code=409, detail="The form hasn't been submitted yet.")
    data = recruit_form.parse(app.data)
    if form == "3822H" and not data.medical.has:
        raise HTTPException(status_code=404, detail="No health declaration: the parent answered no to medical conditions.")
    pdf = build_pdf(data, form, app.parent_signature, app.cadet_signature, app.submitted_at)
    name = "3822A-H" if form == "both" else form
    filename = f"{name} {app.cadet_first_name} {app.cadet_last_name}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition("inline", filename, "recruit.pdf")})


# ── public (the parent's link) ───────────────────────────────────────────────

class SaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: RecruitFormData


class SubmitBody(SaveBody):
    parentSignature: str | None = None
    cadetSignature: str | None = None


def _public_view(app: RecruitApplication) -> dict:
    view = {
        "cadetFirstName": app.cadet_first_name,
        "status": "submitted" if app.submitted_at else "open",
        "expiresAt": app.expires_at.isoformat(),
        "submittedAt": _iso(app.submitted_at),
    }
    if app.submitted_at:
        return view
    form = recruit_form.parse(app.data)
    return {
        **view,
        "data": form.model_dump(mode="json"),
        "missing": recruit_form.missing(form),
        "progress": recruit_form.progress(form, False),
        "lastSavedAt": _iso(app.last_saved_at),
        "options": recruit_form.options(),
    }


@router.get("/recruit-form/{token}")
def open_form(token: str, db: Session = Depends(get_db)):
    app = _by_token(db, token)
    if not app.opened_at:
        app.opened_at = datetime.now()
        db.commit()
    return _public_view(app)


@router.put("/recruit-form/{token}")
def save_progress(token: str, body: SaveBody, db: Session = Depends(get_db)):
    app = _open_for_editing(db, token)
    app.data = body.data.model_dump(mode="json")
    app.last_saved_at = datetime.now()
    db.commit()
    return _public_view(app)


@router.post("/recruit-form/{token}/submit")
def submit_form(token: str, body: SubmitBody, db: Session = Depends(get_db)):
    app = _open_for_editing(db, token)
    form = body.data
    now = datetime.now()

    # Save what they sent either way, so a refused submit loses nothing.
    app.data = form.model_dump(mode="json")
    app.last_saved_at = now
    db.commit()

    gaps = {step: items for step, items in recruit_form.missing(form).items() if items}
    if not form.signer.forename or not form.signer.surname:
        gaps["sign"] = ["Your forename and surname"]
    if gaps:
        raise HTTPException(status_code=422, detail={"message": "Some answers are still needed.", "missing": gaps})

    parent_sig = _decode_signature(body.parentSignature, "parent's")
    cadet_sig = None
    if recruit_form.cadet_signs_health_form(form, now.date()):
        cadet_sig = _decode_signature(body.cadetSignature, "cadet's")

    app.parent_signature = parent_sig
    app.cadet_signature = cadet_sig
    app.submitted_at = now
    db.commit()
    return _public_view(app)
