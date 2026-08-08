"""NCO appraisals — staff write them, the squadron keeps them, the NCO gets a copy.

Staff pick a cadet NCO, the header block (age and attendance) is filled in from
what's already on record, and the five free-text sections are either written by
hand or drafted by the AI writer from a few bullet points. Saved appraisals
download as the squadron's Word template or a PDF, and the PDF can be emailed
straight to the NCO it's about.

Choosing the next review interval is what drives the upcoming list: every
appraisal carries a next-review date, so "who is due" needs no separate tracking.
NCOs who have never been appraised have no such date, which is what the reminders
are for — see NcoAppraisalReminder.

Staff-only throughout. NCOs read their own appraisal from the emailed PDF, not
from here.
"""

import io
import os
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session, selectinload

from database.models import (
    APPRAISAL_SECTIONS, NEXT_REVIEW_MONTHS, Cadet, CadetAttendance, NcoAppraisal,
    NcoAppraisalReminder, User,
)

from core.attendance import ABSENT, PRESENT, count_states
from core.db import get_db, get_or_create_user
from core.emailer import EMAIL_RE, nco_appraisal_email_html, send_email
from core.llm import PRIMARY_MODEL, model_label
from core.security import require_staff
from form_generators.nco_appraisal_gen import build_appraisal_docx, next_review_label
from form_generators.nco_appraisal_pdf import build_appraisal_pdf

router = APIRouter()

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "word_templates", "nco_appraisal_template.docx"
)

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Cadet ranks that make someone part of the NCO team. Checked alongside the
# "NCO" flight, because a newly promoted cadet is usually moved into the flight
# before (or after) their rank catches up in Bader — either marker is enough.
NCO_RANKS = ("Cpl", "Sgt", "FS", "CWO")

# How far back the auto-filled attendance figure looks. A year covers the whole
# training cycle, which is the period an appraisal is judging.
ATTENDANCE_WINDOW_DAYS = 365

MAX_AI_POINTS = 8000


# ── request models ───────────────────────────────────────────────────────────

class AppraisalBody(BaseModel):
    """The form. Every text field is optional so a half-written appraisal still
    saves; the cadet and the review interval are the only things required."""
    cadet_id: int
    appraisal_date: str | None = None  # ISO date; defaults to today

    # Header block. Left blank, each falls back to the figure computed from the
    # cadet's record — staff can still override what gets printed.
    nco_name: str | None = None
    age: str | None = None
    attendance: str | None = None

    general_observations: str = ""
    effectiveness_in_role: str = ""
    strengths: str = ""
    weaknesses: str = ""
    targets: str = ""

    next_review_months: int = 12
    cause_for_concern: bool = False
    extend_probation: bool = False

    # Set by the client when the sections came from the AI writer, so the saved
    # appraisal records that it was drafted rather than written from scratch.
    generated_by: str | None = None


class ReminderBody(BaseModel):
    cadet_id: int
    due_date: str
    note: str = ""


class AiBody(BaseModel):
    """Staff notes to draft from. The header fields are passed through so the
    draft can quote the NCO's name and figures before anything is saved."""
    cadet_id: int
    points: str
    nco_name: str | None = None
    age: str | None = None
    attendance: str | None = None


class EmailBody(BaseModel):
    to: str | None = None        # defaults to the NCO's own address on record
    reply_to: str | None = None  # defaults to the staff member sending it

    @field_validator("to", "reply_to")
    @classmethod
    def _looks_like_an_email(cls, value: str | None) -> str | None:
        """Only checked when the field is filled in — both fields have a server
        side default, and blank means "use it"."""
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not EMAIL_RE.match(value):
            raise ValueError("must be an email address")
        return value


# ── helpers ──────────────────────────────────────────────────────────────────

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_date(value: str | None, field: str) -> datetime | None:
    """Accept the browser's "YYYY-MM-DD" (or a full ISO timestamp). Anything
    else is a client bug, so reject it rather than silently dropping the date."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}")


def add_months(start: datetime, months: int) -> datetime:
    """`start` shifted by whole months, clamped to the end of the target month
    so 31 Jan + 1 month is 28 Feb rather than rolling into March."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    return start.replace(year=year, month=month,
                         day=min(start.day, monthrange(year, month)[1]))


def is_nco(cadet: Cadet) -> bool:
    if (cadet.flight or "").strip().upper() == "NCO":
        return True
    rank = (cadet.rank or "").strip()
    return any(rank.startswith(r) for r in NCO_RANKS)


def cadet_name(cadet: Cadet) -> str:
    """As the appraisal prints it — rank first, e.g. "Cpl Sawczuk"."""
    return " ".join(
        part for part in [(cadet.rank or "").strip(), cadet.first_name, cadet.last_name]
        if part
    )


def age_on(cadet: Cadet, when: date) -> str:
    """Whole years on `when`, as a string for the form. "" when there's no date
    of birth on record rather than a guess."""
    dob = cadet.date_of_birth
    if not dob:
        return ""
    years = when.year - dob.year - ((when.month, when.day) < (dob.month, dob.day))
    return str(years) if years >= 0 else ""


def attendance_summary(records) -> str:
    """The auto-filled attendance figure, e.g. "85% (17/20 nights, last 12 months)".

    Authorised absences are left out of the percentage entirely — an excused
    absence isn't a turnout failure, and counting it as one would understate
    every NCO who ever told us they couldn't make it.
    """
    cutoff = datetime.now() - timedelta(days=ATTENDANCE_WINDOW_DAYS)
    counts = count_states([r for r in records if r.date and r.date >= cutoff])
    eligible = counts[PRESENT] + counts[ABSENT]
    if not eligible:
        return ""
    rate = round(counts[PRESENT] / eligible * 100)
    return f"{rate}% ({counts[PRESENT]}/{eligible} nights, last 12 months)"


def _prefill(cadet: Cadet, records, when: date) -> dict:
    """The header block as computed from the cadet's record."""
    return {
        "nco_name": cadet_name(cadet),
        "age": age_on(cadet, when),
        "attendance": attendance_summary(records),
    }


def _author_name(user: User | None) -> str:
    if not user:
        return "Unknown"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email


def _get_or_404(db: Session, appraisal_id: int) -> NcoAppraisal:
    appraisal = (
        db.query(NcoAppraisal)
        .filter(NcoAppraisal.id == appraisal_id)
        .options(selectinload(NcoAppraisal.cadet), selectinload(NcoAppraisal.author))
        .first()
    )
    if not appraisal:
        raise HTTPException(status_code=404, detail="Appraisal not found")
    return appraisal


def _nco_or_400(db: Session, cadet_id: int) -> Cadet:
    cadet = db.query(Cadet).filter(Cadet.cin == cadet_id).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cadet_id} not found")
    if not is_nco(cadet):
        raise HTTPException(
            status_code=400,
            detail=f"{cadet.first_name} {cadet.last_name} isn't in the NCO team",
        )
    return cadet


def _summary(appraisal: NcoAppraisal) -> dict:
    cadet = appraisal.cadet
    return {
        "id": appraisal.id,
        "cadet_id": appraisal.cadet_id,
        "nco_name": appraisal.nco_name,
        "rank": cadet.rank if cadet else None,
        "appraisal_date": _iso(appraisal.appraisal_date),
        "next_review_months": appraisal.next_review_months,
        "next_review_date": _iso(appraisal.next_review_date),
        "cause_for_concern": appraisal.cause_for_concern,
        "extend_probation": appraisal.extend_probation,
        "author_name": _author_name(appraisal.author),
        "emailed_at": _iso(appraisal.emailed_at),
        "emailed_to": appraisal.emailed_to,
        "created_at": _iso(appraisal.created_at),
        "updated_at": _iso(appraisal.updated_at),
    }


def _detail(appraisal: NcoAppraisal) -> dict:
    cadet = appraisal.cadet
    return {
        **_summary(appraisal),
        "age": appraisal.age,
        "attendance": appraisal.attendance,
        **{key: getattr(appraisal, key) or "" for key in APPRAISAL_SECTIONS},
        "cadet_email": cadet.email if cadet else None,
        "generated_by": appraisal.generated_by,
        "generated_by_label": (
            model_label(appraisal.generated_by) if appraisal.generated_by else None
        ),
        "generated_with_fallback": (
            bool(appraisal.generated_by) and appraisal.generated_by != PRIMARY_MODEL
        ),
    }


def _reminder_json(reminder: NcoAppraisalReminder) -> dict:
    cadet = reminder.cadet
    return {
        "id": reminder.id,
        "cadet_id": reminder.cadet_id,
        "nco_name": cadet_name(cadet) if cadet else "",
        "due_date": _iso(reminder.due_date),
        "note": reminder.note,
        "created_by_name": reminder.created_by_name,
        "created_at": _iso(reminder.created_at),
    }


def _apply_body(appraisal: NcoAppraisal, body: AppraisalBody, cadet: Cadet,
                records) -> None:
    """Write the form onto the row, deriving what the form left blank."""
    if body.next_review_months not in NEXT_REVIEW_MONTHS:
        raise HTTPException(
            status_code=400,
            detail=f"Next review must be one of {', '.join(map(str, NEXT_REVIEW_MONTHS))} months",
        )

    appraisal.appraisal_date = _parse_date(body.appraisal_date, "appraisal date") or datetime.now()
    computed = _prefill(cadet, records, appraisal.appraisal_date.date())
    for field in ("nco_name", "age", "attendance"):
        supplied = getattr(body, field)
        # None means "the form didn't send it"; an empty string means staff
        # deliberately cleared it, so only None falls back to the computed value.
        setattr(appraisal, field, computed[field] if supplied is None else supplied.strip())

    for key in APPRAISAL_SECTIONS:
        setattr(appraisal, key, (getattr(body, key) or "").strip())

    appraisal.next_review_months = body.next_review_months
    appraisal.next_review_date = add_months(appraisal.appraisal_date, body.next_review_months)
    appraisal.cause_for_concern = body.cause_for_concern
    appraisal.extend_probation = body.extend_probation
    appraisal.generated_by = body.generated_by or None
    appraisal.updated_at = datetime.now()


def _document_filename(appraisal: NcoAppraisal, extension: str) -> str:
    name = "".join(
        c for c in (appraisal.nco_name or "NCO") if c.isalnum() or c in " -_"
    ).strip().replace(" ", "_") or "NCO"
    stamp = (appraisal.appraisal_date or datetime.now()).strftime("%Y%m%d")
    return f"NCO_Appraisal_{name}_{stamp}.{extension}"


# ── overview ─────────────────────────────────────────────────────────────────

@router.get("/nco-appraisals")
def list_appraisals(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Everything the appraisals page needs: the NCO team with their auto-filled
    figures, every appraisal on record, the open reminders, and the merged
    "coming up" list in due-date order."""
    today = datetime.now()

    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    ncos = [c for c in cadets if is_nco(c)]

    # Attendance for the NCO team only, and only inside the window the summary
    # looks at — eager-loading Cadet.attendance would drag the whole squadron's
    # register (years of rows, most of them for cadets not on this page) into
    # memory to compute a dozen percentages.
    records: dict[int, list] = defaultdict(list)
    if ncos:
        cutoff = today - timedelta(days=ATTENDANCE_WINDOW_DAYS)
        for record in (
            db.query(CadetAttendance)
            .filter(CadetAttendance.cadet_id.in_([c.cin for c in ncos]),
                    CadetAttendance.date >= cutoff)
            .all()
        ):
            records[record.cadet_id].append(record)

    appraisals = (
        db.query(NcoAppraisal)
        .options(selectinload(NcoAppraisal.cadet), selectinload(NcoAppraisal.author))
        .order_by(NcoAppraisal.appraisal_date.desc(), NcoAppraisal.id.desc())
        .all()
    )
    reminders = (
        db.query(NcoAppraisalReminder)
        .options(selectinload(NcoAppraisalReminder.cadet))
        .order_by(NcoAppraisalReminder.due_date)
        .all()
    )

    # Latest appraisal per cadet — the list is already newest-first, so the first
    # one seen for a cadet is the one that sets their next review date.
    latest: dict[int, NcoAppraisal] = {}
    counts: dict[int, int] = {}
    for appraisal in appraisals:
        latest.setdefault(appraisal.cadet_id, appraisal)
        counts[appraisal.cadet_id] = counts.get(appraisal.cadet_id, 0) + 1
    reminder_by_cadet = {r.cadet_id: r for r in reminders}

    nco_rows, upcoming = [], []
    for cadet in ncos:
        last = latest.get(cadet.cin)
        reminder = reminder_by_cadet.get(cadet.cin)
        nco_rows.append({
            "cin": cadet.cin,
            "rank": cadet.rank,
            "first_name": cadet.first_name,
            "last_name": cadet.last_name,
            "flight": cadet.flight,
            "email": cadet.email,
            **_prefill(cadet, records[cadet.cin], today.date()),
            "last_appraisal_id": last.id if last else None,
            "last_appraisal_date": _iso(last.appraisal_date) if last else None,
            "next_review_date": _iso(last.next_review_date) if last else None,
            "appraisal_count": counts.get(cadet.cin, 0),
            "reminder_id": reminder.id if reminder else None,
        })

        # An appraisal's own next-review date wins: once one exists, that's the
        # real schedule and any reminder is stale bookkeeping.
        due, source = (
            (last.next_review_date, "appraisal") if last
            else (reminder.due_date, "reminder") if reminder
            else (None, None)
        )
        if due:
            upcoming.append({
                "cin": cadet.cin,
                "nco_name": cadet_name(cadet),
                "due_date": _iso(due),
                "source": source,
                "overdue": due < today,
                "days_until": (due.date() - today.date()).days,
                "appraisal_id": last.id if last else None,
                "reminder_id": reminder.id if reminder and not last else None,
                "note": reminder.note if reminder and not last else "",
                "last_appraisal_date": _iso(last.appraisal_date) if last else None,
            })

    upcoming.sort(key=lambda row: row["due_date"])

    return {
        "ncos": nco_rows,
        "appraisals": [_summary(a) for a in appraisals],
        "reminders": [_reminder_json(r) for r in reminders],
        "upcoming": upcoming,
        # NCOs with neither an appraisal nor a reminder — the gap the reminder
        # button is there to close.
        "unscheduled": [
            row["cin"] for row in nco_rows
            if not row["next_review_date"] and not row["reminder_id"]
        ],
        "next_review_options": list(NEXT_REVIEW_MONTHS),
        "ai_available": True,
        "primary_model_label": model_label(PRIMARY_MODEL),
    }


# ── reminders (declared before /{appraisal_id} so the path isn't swallowed) ───

@router.post("/nco-appraisals/reminders")
def upsert_reminder(
    body: ReminderBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Put a date on an NCO who hasn't been appraised yet, so they appear on the
    upcoming list. One per cadet — sending a second one moves the existing date
    rather than stacking up duplicates nobody will clear."""
    cadet = _nco_or_400(db, body.cadet_id)
    due = _parse_date(body.due_date, "due date")
    if not due:
        raise HTTPException(status_code=400, detail="A due date is required")

    # Read the author's name off the User row *before* a half-built reminder is
    # in the session: get_or_create_user commits, so the first attribute access
    # afterwards lazy-loads, and that autoflushes whatever is pending.
    author = _author_name(get_or_create_user(db, idinfo))

    reminder = (
        db.query(NcoAppraisalReminder)
        .filter(NcoAppraisalReminder.cadet_id == cadet.cin)
        .first()
    )
    if not reminder:
        reminder = NcoAppraisalReminder(cadet_id=cadet.cin, created_at=datetime.now())
        db.add(reminder)
    reminder.due_date = due
    reminder.note = body.note.strip()[:2000]
    reminder.created_by_name = author
    reminder.created_by_email = idinfo.get("email", "")
    db.commit()
    db.refresh(reminder)
    return _reminder_json(reminder)


@router.delete("/nco-appraisals/reminders/{reminder_id}")
def delete_reminder(
    reminder_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    reminder = (
        db.query(NcoAppraisalReminder)
        .filter(NcoAppraisalReminder.id == reminder_id)
        .first()
    )
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    db.delete(reminder)
    db.commit()
    return {"ok": True}


# ── AI drafting ──────────────────────────────────────────────────────────────

@router.post("/nco-appraisals/ai")
def draft_with_ai(  # sync on purpose — the slow AI call runs in the threadpool
    body: AiBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Draft the five sections from a few bullet points of staff notes.

    Nothing is saved — the draft goes back into the form for staff to correct,
    which is the only way an AI-written appraisal should ever reach an NCO.
    """
    from scripts.nco_appraisal_ai import generate_appraisal

    points = (body.points or "").strip()
    if len(points) < 20:
        raise HTTPException(
            status_code=400,
            detail="Write a few more points first — there isn't enough here to write an appraisal from",
        )

    cadet = _nco_or_400(db, body.cadet_id)
    computed = _prefill(cadet, cadet.attendance, datetime.now().date())

    try:
        sections, model_id = generate_appraisal(
            name=body.nco_name or computed["nco_name"],
            age=body.age or computed["age"],
            attendance=body.attendance or computed["attendance"],
            points=points[:MAX_AI_POINTS],
        )
    except Exception as e:
        print(f"[draft_with_ai] generation failed: {e}")
        raise HTTPException(
            status_code=502,
            detail="The AI writer couldn't be reached. Try again, or write the sections by hand.",
        )

    if not any(sections.values()):
        raise HTTPException(
            status_code=502,
            detail="The AI writer returned nothing usable. Try again with a bit more detail.",
        )

    return {
        "sections": sections,
        "model": model_id,
        "model_label": model_label(model_id),
        "used_fallback": model_id != PRIMARY_MODEL,
    }


# ── appraisals ───────────────────────────────────────────────────────────────

@router.post("/nco-appraisals")
def create_appraisal(
    body: AppraisalBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = _nco_or_400(db, body.cadet_id)
    user = get_or_create_user(db, idinfo)

    now = datetime.now()
    appraisal = NcoAppraisal(
        cadet_id=cadet.cin, author_id=user.id, created_at=now, updated_at=now,
        appraisal_date=now, next_review_date=now,
    )
    _apply_body(appraisal, body, cadet, cadet.attendance)
    db.add(appraisal)

    # The appraisal is what the reminder was asking for, so it has served its
    # purpose — the next review date now comes from the appraisal itself.
    db.query(NcoAppraisalReminder).filter(
        NcoAppraisalReminder.cadet_id == cadet.cin
    ).delete()

    db.commit()
    db.refresh(appraisal)
    return _detail(appraisal)


@router.get("/nco-appraisals/{appraisal_id}")
def get_appraisal(
    appraisal_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return _detail(_get_or_404(db, appraisal_id))


@router.put("/nco-appraisals/{appraisal_id}")
def update_appraisal(
    appraisal_id: int,
    body: AppraisalBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    appraisal = _get_or_404(db, appraisal_id)
    if body.cadet_id != appraisal.cadet_id:
        raise HTTPException(
            status_code=400,
            detail="An appraisal can't be moved to a different NCO — write a new one",
        )
    # Not _nco_or_400: an NCO who has since been demoted (or aged out) still has
    # an appraisal on record, and staff must be able to correct a typo in it.
    cadet = appraisal.cadet
    if not cadet:
        raise HTTPException(status_code=404, detail="The NCO this appraisal is about no longer exists")
    _apply_body(appraisal, body, cadet, cadet.attendance)
    db.commit()
    db.refresh(appraisal)
    return _detail(appraisal)


@router.delete("/nco-appraisals/{appraisal_id}")
def delete_appraisal(
    appraisal_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    appraisal = _get_or_404(db, appraisal_id)
    db.delete(appraisal)
    db.commit()
    return {"ok": True}


@router.get("/nco-appraisals/{appraisal_id}/document")
def download_appraisal(
    appraisal_id: int,
    fmt: str = Query("pdf", pattern="^(pdf|docx)$"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The appraisal as the squadron's Word template (`fmt=docx`) or a PDF."""
    appraisal = _get_or_404(db, appraisal_id)

    buffer = io.BytesIO()
    if fmt == "docx":
        build_appraisal_docx(TEMPLATE_PATH, buffer, appraisal)
        media_type = DOCX_MEDIA_TYPE
    else:
        buffer.write(build_appraisal_pdf(appraisal))
        media_type = "application/pdf"
    buffer.seek(0)

    filename = _document_filename(appraisal, fmt)
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/nco-appraisals/{appraisal_id}/email")
def email_appraisal(
    appraisal_id: int,
    body: EmailBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Send the PDF to the NCO it's about.

    Replies default to the staff member who sent it rather than the unmonitored
    noreply account — an NCO who wants to talk about their appraisal should reach
    the person who wrote it.
    """
    appraisal = _get_or_404(db, appraisal_id)
    to = (body.to or (appraisal.cadet.email if appraisal.cadet else "") or "").strip()
    if not to:
        raise HTTPException(
            status_code=400,
            detail="This NCO has no email address on record — enter one to send to",
        )

    pdf = build_appraisal_pdf(appraisal)
    reply_to = (body.reply_to or idinfo.get("email", "")).strip() or None
    send_email(
        to,
        f"Your NCO appraisal — {appraisal.appraisal_date.strftime('%d/%m/%Y')}",
        nco_appraisal_email_html(
            appraisal.nco_name or "there",
            appraisal.appraisal_date.strftime("%d/%m/%Y"),
            next_review_label(appraisal.next_review_months, appraisal.next_review_date),
            _author_name(appraisal.author),
            reply_to,
        ),
        attachments=[(_document_filename(appraisal, "pdf"), pdf, "application/pdf")],
        reply_to=reply_to,
    )

    # send_email swallows its own failures, so this stamp means "we tried", not
    # "it was delivered" — a bounce lands in the noreply mailbox, not here.
    appraisal.emailed_at = datetime.now()
    appraisal.emailed_to = to
    db.commit()
    db.refresh(appraisal)
    return _detail(appraisal)
