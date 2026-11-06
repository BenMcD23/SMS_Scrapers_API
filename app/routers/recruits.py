"""New recruits' joining forms (RAFAC 3822A consent + 3822H health declaration).

Staff add a recruit on SMS and get a link to send the parent. The parent fills
the form in at home on the squadron website, saving as they go, then signs and
submits. Staff watch the progress on SMS and, once it's in, read the answers
and download the filled official PDFs.

Two halves:
  /recruits/...         staff only, behind the usual Google sign-in
  /recruit-form/{token} public; the token in the link is the only credential

Recruits can also sign themselves up on a recruitment evening, from a QR code
on the night (/recruit-signup, the website's /enrol). It replaces the paper or
Google sheet parents used to fill in: they give what staff would have typed into
"Add recruit", and the joining-form link is emailed to them. The page is shut
unless staff open it from SMS, and it shuts itself again at midnight.

The public half answers every bad link — unknown, expired, deleted — with the
same 404, so a link can't be probed for whether it ever existed. It never sends
answers back once the form is submitted.

Once submitted, the parent is emailed a copy of the signed forms, with the
recruiting contact (RECRUIT_CONTACT_EMAIL) as Reply-To for any problems.

Times are UK local time: "aged 16 at the date of signature" is a UK date, and
the server's clock isn't on UK time.
"""

import base64
import binascii
import logging
import re
import secrets
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import func
from sqlalchemy.orm import Session

from core import recruit_form
from core.config import RECRUIT_CONTACT_EMAIL, RECRUIT_CONTACT_NAME, RECRUIT_FORM_URL
from core.db import get_db
from core.emailer import EMAIL_RE, recruit_form_confirmation_html, recruit_form_link_html, send_email
from core.http import content_disposition
from core.ratelimit import limiter
from core.recruit_form import RecruitFormData, london_now
from core.security import require_staff
from database.models import RecruitApplication, RecruitSignupSettings
from form_generators.recruit_pdf import build_pdf

router = APIRouter()
logger = logging.getLogger(__name__)

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
    if not app or (not app.submitted_at and app.expires_at <= london_now()):
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


def _prefill(cadet_name: str, parent_name: str, parent_email: str,
             dob: date | None = None, mobile: str = "") -> dict:
    """What's already known (from staff, or the sign-up page), so the parent
    doesn't type it twice."""
    forename, _, surname = parent_name.partition(" ")
    return RecruitFormData.model_validate({
        "cadet": {"fullName": cadet_name, "dob": dob},
        "nok": {"fullName": parent_name, "email": parent_email, "mobile": mobile},
        "signer": {"forename": forename, "surname": surname},
    }).model_dump(mode="json")


def _new_application(first: str, last: str, parent_name: str, parent_email: str, now: datetime,
                     created_by: str | None, dob: date | None = None, mobile: str = "") -> RecruitApplication:
    return RecruitApplication(
        cadet_first_name=first,
        cadet_last_name=last,
        parent_name=parent_name,
        parent_email=parent_email,
        token=secrets.token_urlsafe(32),
        expires_at=now + timedelta(days=LINK_DAYS),
        data=_prefill(f"{first} {last}", parent_name, parent_email, dob, mobile),
        created_by=created_by,
        created_at=now,
    )


def _confirmation_recipients(form: RecruitFormData, link_email: str) -> list[str]:
    """The email the parent gave on the form, plus the one staff sent the link
    to if that's different (it's known to reach them). Order kept, no repeats."""
    out: list[str] = []
    for addr in (form.nok.email, link_email):
        addr = (addr or "").strip()
        if EMAIL_RE.match(addr) and addr.lower() not in (a.lower() for a in out):
            out.append(addr)
    return out


def _send_confirmation(form: RecruitFormData, link_email: str, cadet_first: str, cadet_last: str,
                       parent_sig: bytes, cadet_sig: bytes | None, submitted_at: datetime) -> None:
    """Email the parent a copy of the forms they just signed.

    Runs after the response: the submission is already saved, so a PDF or Gmail
    failure is logged and never undoes it (staff still have the forms on SMS).
    """
    to = _confirmation_recipients(form, link_email)
    if not to:
        return
    forms = ["3822A", "3822H"] if form.medical.has else ["3822A"]
    try:
        attachments = [
            (f"{f} {cadet_first} {cadet_last}.pdf",
             build_pdf(form, f, parent_sig, cadet_sig, submitted_at, cadet_name=(cadet_first, cadet_last)),
             "application/pdf")
            for f in forms
        ]
    except Exception:
        logger.exception("couldn't build the joining form PDFs for the confirmation email")
        return
    send_email(
        ", ".join(to),
        f"{cadet_first}'s joining form for 317 Squadron",
        recruit_form_confirmation_html(cadet_first, forms, RECRUIT_CONTACT_NAME, RECRUIT_CONTACT_EMAIL),
        attachments=attachments,
        reply_to=RECRUIT_CONTACT_EMAIL,
    )


# ── recruitment-evening sign-up ──────────────────────────────────────────────

# created_by on a recruit who signed up themselves, where staff would be named.
SIGNUP_CREATED_BY = "sign-up page"
SIGNUP_CLOSED = "Sign-ups aren't open at the moment. Ask a member of staff."
# A child old enough to join soon, up to the last chance to join: anything
# outside this is a mistyped year, not a recruit.
SIGNUP_AGES = (9, 18)


def _signup_settings(db: Session) -> RecruitSignupSettings:
    row = db.query(RecruitSignupSettings).first()
    if not row:
        row = RecruitSignupSettings()
        db.add(row)
        db.flush()
    return row


def _signup_open(row: RecruitSignupSettings, now: datetime) -> bool:
    return bool(row.open_until and row.open_until > now)


def _signup_view(row: RecruitSignupSettings, now: datetime) -> dict:
    is_open = _signup_open(row, now)
    return {
        "open": is_open,
        "openUntil": _iso(row.open_until) if is_open else None,
        "openedBy": row.opened_by if is_open else None,
        # The QR code's target: the joining form's address without a token.
        "url": RECRUIT_FORM_URL,
    }


class SignupSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: bool


@router.get("/recruits/sign-up")
def get_signup(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    row = _signup_settings(db)
    db.commit()
    return _signup_view(row, london_now())


@router.put("/recruits/sign-up")
def set_signup(body: SignupSwitch, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Open the sign-up page until midnight tonight (UK), or close it now."""
    now = london_now()
    row = _signup_settings(db)
    if body.open:
        row.open_until = datetime.combine(now.date() + timedelta(days=1), time.min)
        row.opened_by = idinfo.get("email")
    else:
        row.open_until = None
    db.commit()
    return _signup_view(row, now)


class Signup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadetFirstName: Name
    cadetLastName: Name
    cadetDob: date
    parentName: Name
    parentEmail: Name
    parentMobile: Name
    # A field people never see: a bot fills every input, a parent leaves it blank.
    website: str = ""


def _send_link(to: str, cadet_first: str, link: str, expires: datetime) -> None:
    """Runs after the response, so it takes plain values: the request's
    database session is closed by then."""
    send_email(
        to,
        f"{cadet_first}'s joining form for 317 Squadron",
        recruit_form_link_html(cadet_first, link, f"{expires.day} {expires:%B %Y}",
                               RECRUIT_CONTACT_NAME, RECRUIT_CONTACT_EMAIL),
        reply_to=RECRUIT_CONTACT_EMAIL,
    )


def _queue_link(background: BackgroundTasks, app: RecruitApplication) -> None:
    background.add_task(_send_link, app.parent_email, app.cadet_first_name, _link(app), app.expires_at)


@router.get("/recruit-signup")
def signup_status(db: Session = Depends(get_db)):
    """Whether the page should show the form or say it's shut. Nothing else."""
    row = _signup_settings(db)
    db.commit()
    return {"open": _signup_open(row, london_now())}


@router.post("/recruit-signup", status_code=201)
# Public, and every request arrives through the same proxy, so this caps the
# whole page rather than one phone: well above a busy evening's families, far
# below what a script would want.
@limiter.limit("30/minute")
def signup(request: Request, body: Signup, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Add a recruit from the sign-up page and email the parent their link.

    The response carries the link so the parent can start straight away, but
    only for a new recruit: if this child is already waiting under the same
    email, the link is emailed again and not shown, so typing someone's name
    and email can't open a form with their answers in.
    """
    now = london_now()
    if not _signup_open(_signup_settings(db), now):
        db.commit()
        raise HTTPException(status_code=409, detail=SIGNUP_CLOSED)
    if body.website:
        return {"cadetFirstName": body.cadetFirstName, "link": None, "email": body.parentEmail}
    if not _EMAIL.match(body.parentEmail):
        raise HTTPException(status_code=422, detail="Enter your email address, like name@example.com.")
    if not recruit_form._phone_ok(body.parentMobile):
        raise HTTPException(status_code=422, detail="Enter your mobile number, like 07700 900123.")
    age = recruit_form.age_on(body.cadetDob, now.date())
    if age is None or not SIGNUP_AGES[0] <= age <= SIGNUP_AGES[1]:
        raise HTTPException(status_code=422, detail="Check your child's date of birth.")

    existing = (db.query(RecruitApplication)
                .filter(RecruitApplication.submitted_at.is_(None))
                .filter(func.lower(RecruitApplication.cadet_first_name) == body.cadetFirstName.lower())
                .filter(func.lower(RecruitApplication.cadet_last_name) == body.cadetLastName.lower())
                .filter(func.lower(RecruitApplication.parent_email) == body.parentEmail.lower())
                .first())
    if existing:
        # A second go (a double tap, or the email didn't arrive): one recruit,
        # with a live link, sent again.
        if existing.expires_at <= now:
            existing.expires_at = now + timedelta(days=LINK_DAYS)
        db.commit()
        _queue_link(background, existing)
        return {"cadetFirstName": existing.cadet_first_name, "link": None, "email": existing.parent_email}

    app = _new_application(body.cadetFirstName, body.cadetLastName, body.parentName, body.parentEmail,
                           now, SIGNUP_CREATED_BY, body.cadetDob, body.parentMobile)
    db.add(app)
    db.commit()
    _queue_link(background, app)
    return {"cadetFirstName": app.cadet_first_name, "link": _link(app), "email": app.parent_email}


# ── staff ────────────────────────────────────────────────────────────────────

class NewRecruit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadetFirstName: Name
    cadetLastName: Name
    parentName: Name
    parentEmail: Name


@router.get("/recruits")
def list_recruits(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    now = london_now()
    apps = db.query(RecruitApplication).order_by(RecruitApplication.created_at.desc()).all()
    return [_summary(a, now) for a in apps]


@router.post("/recruits", status_code=201)
def add_recruit(body: NewRecruit, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    if not _EMAIL.match(body.parentEmail):
        raise HTTPException(status_code=422, detail="Enter the parent's email address, like name@example.com.")
    now = london_now()
    app = _new_application(body.cadetFirstName, body.cadetLastName, body.parentName, body.parentEmail,
                           now, idinfo.get("email"))
    db.add(app)
    db.commit()
    return _summary(app, now)


@router.get("/recruits/{recruit_id}")
def get_recruit(recruit_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    app = _get(db, recruit_id)
    form = recruit_form.parse(app.data)
    return {
        **_summary(app, london_now()),
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
    now = london_now()
    app.expires_at = now + timedelta(days=LINK_DAYS)
    db.commit()
    return _summary(app, now)


@router.post("/recruits/{recruit_id}/reopen")
def reopen_form(recruit_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Send a submitted form back to the parent to correct. Their answers are
    kept, the signatures are not (they sign again, against the corrected
    answers), and the same link works for another LINK_DAYS."""
    app = _get(db, recruit_id)
    if not app.submitted_at:
        raise HTTPException(status_code=409, detail="This form hasn't been submitted yet, so there's nothing to reopen.")
    now = london_now()
    app.submitted_at = None
    app.parent_signature = None
    app.cadet_signature = None
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
    pdf = build_pdf(data, form, app.parent_signature, app.cadet_signature, app.submitted_at,
                    cadet_name=(app.cadet_first_name, app.cadet_last_name))
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
        app.opened_at = london_now()
        db.commit()
    return _public_view(app)


@router.put("/recruit-form/{token}")
def save_progress(token: str, body: SaveBody, db: Session = Depends(get_db)):
    app = _open_for_editing(db, token)
    app.data = body.data.model_dump(mode="json")
    app.last_saved_at = london_now()
    db.commit()
    return _public_view(app)


@router.post("/recruit-form/{token}/submit")
def submit_form(token: str, body: SubmitBody, background: BackgroundTasks, db: Session = Depends(get_db)):
    app = _open_for_editing(db, token)
    form = body.data
    now = london_now()

    # Save what they sent either way, so a refused submit loses nothing.
    app.data = form.model_dump(mode="json")
    app.last_saved_at = now
    db.commit()

    gaps = {step: items for step, items in recruit_form.missing(form, now.date()).items() if items}
    if not form.signer.forename or not form.signer.surname:
        gaps["sign"] = ["Your forename and surname"]
    if gaps:
        raise HTTPException(status_code=422, detail={"message": "Some answers are still needed.", "missing": gaps})

    parent_sig = _decode_signature(body.parentSignature, "parent's")
    cadet_sig = None
    if recruit_form.cadet_signs_health_form(form, now.date()):
        cadet_sig = _decode_signature(body.cadetSignature, "cadet's")

    # What's signed is only what the parent finally answered.
    app.data = recruit_form.tidy(form).model_dump(mode="json")
    app.parent_signature = parent_sig
    app.cadet_signature = cadet_sig
    app.submitted_at = now
    db.commit()
    background.add_task(_send_confirmation, recruit_form.parse(app.data), app.parent_email,
                        app.cadet_first_name, app.cadet_last_name, parent_sig, cadet_sig, now)
    return _public_view(app)
