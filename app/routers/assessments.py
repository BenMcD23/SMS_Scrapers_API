"""Assessment sheets — creation, overview, PDFs, and uploading to Bader."""

from collections import defaultdict
from datetime import datetime
from functools import partial
import io

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.models import AssessmentSheet, Cadet, User

from scripts.scraper_calls import upload_qualifications_scraper

from assessment_builders.leadership import generate_leadership_pdf, process_assessment_data
from assessment_builders.radio import generate_radio_pdf, process_radio_data
from assessment_builders.moi import generate_moi_pdf, process_assessment_data as process_moi_data

from core.db import get_db, get_or_create_user
from core.emailer import send_email, assessment_email_html
from core.security import require_staff, require_staff_or_nco
from routers import scrapers

router = APIRouter()


class UploadQualificationsRequest(BaseModel):
    assessment_ids: list[int]


class MarkCompleteRequest(BaseModel):
    completed: bool = True


# Assessment types that support in-place editing / PDF regeneration
EDITABLE_TYPES = ("Blue Leadership", "Blue Radio", "MOI")


# How many passed assessments are needed per type before upload is unlocked
UPLOAD_THRESHOLDS: dict[str, int] = {
    "Blue Leadership": 2,
    # everything else defaults to 1
}


def required_passes(assessment_type: str) -> int:
    return UPLOAD_THRESHOLDS.get(assessment_type, 1)


def _resolve_cadet(db: Session, data: dict, allow_name_fallback: bool = True) -> Cadet:
    """Find the cadet by CIN (preferred) or full name."""
    cadet_cin = data.get("cadet_cin")
    if cadet_cin:
        cadet = db.query(Cadet).filter(Cadet.cin == int(cadet_cin)).first()
        if not cadet:
            raise HTTPException(status_code=404, detail=f"Cadet with CIN {cadet_cin} not found.")
        return cadet

    if not allow_name_fallback:
        raise HTTPException(status_code=400, detail="cadet_cin is required.")

    cadet_name = data.get("cadet_name", "").strip()
    if not cadet_name:
        raise HTTPException(status_code=400, detail="cadet_cin or cadet_name is required.")
    cadet = db.query(Cadet).filter(
        (Cadet.first_name + " " + Cadet.last_name).ilike(cadet_name)
    ).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet '{cadet_name}' not found.")
    return cadet


def _assessor_name(user: User) -> str:
    profile_name = user.profile.assessor_name if user.profile else None
    return profile_name or f"{user.first_name or ''} {user.last_name or ''}".strip()


def _save_sheet_and_notify(
    db: Session, user: User, cadet: Cadet,
    assessment_type: str, fields: dict, pdf_bytes: bytes,
) -> AssessmentSheet:
    """Store the sheet, then email the cadet their assessment result."""
    sheet = AssessmentSheet(
        assessment_type=assessment_type,
        fields=fields,
        pdf_data=pdf_bytes,
        uploaded=False,
        pdf_mime_type="application/pdf",
        created_at=datetime.utcnow(),
        cadet_id=cadet.cin,
        assessor_id=user.id,
    )
    db.add(sheet)
    db.commit()
    db.refresh(sheet)

    if cadet.email:
        send_email(
            to=cadet.email,
            subject=f"Assessment Completed ({assessment_type})",
            html_body=assessment_email_html(
                cadet_name=f"{cadet.first_name} {cadet.last_name}",
                assessment_type=assessment_type,
                passed=fields.get("passed"),
                date=fields.get("date"),
                assessor_name=fields.get("assessor_name", ""),
            ),
            attachment=pdf_bytes,
            attachment_filename=f"{assessment_type.replace(' ', '_')}_{cadet.last_name}_{cadet.first_name}.pdf",
        )
    return sheet


# ── Build fields + PDF (shared by create and edit) ────────────────────────────
#
# Each helper takes the raw request `data` (with assessor identity already
# injected by the caller) and returns the `fields` dict to persist plus the
# regenerated PDF bytes. `fields` stores everything needed to faithfully
# rebuild the PDF later — including the signature(s) and the original ISO
# dates — so an assessment can be edited and its PDF remade.

def _leadership_fields_and_pdf(data: dict) -> tuple[dict, bytes]:
    processed = process_assessment_data(data)
    pdf_bytes = generate_leadership_pdf(processed)
    fields = {
        "scores":             processed["scores"],
        "total_score":        processed["total_score"],
        "passed":             processed["passed"],
        "exercise_no":        processed["exercise_no"],
        "exercise_name":      processed["exercise_name"],
        "assessor_name":      processed["assessor_name"],
        "date":               processed["date"],
        "date_iso":           data.get("date", ""),
        "debriefing_notes":   processed["debriefing_notes"],
        "assessor_signature": processed.get("assessor_signature") or "",
    }
    return fields, pdf_bytes


def _radio_fields_and_pdf(data: dict, cadet: Cadet) -> tuple[dict, bytes]:
    processed = process_radio_data(data, cadet)
    pdf_bytes = generate_radio_pdf(processed)
    fields = {
        "criteria":           processed["criteria"],
        "passed":             processed["passed"],
        "cyber_sec_date":     processed["cyber_sec_date"],
        "cyber_sec_date_iso": data.get("cyber_sec_date", ""),
        "comments":           processed["comments"],
        "assessor_name":      processed["assessor_name"],
        "assessor_initials":  processed["assessor_initials"],
        "date":               processed["date"],
        "date_iso":           data.get("date", ""),
        "assessor_signature": processed.get("assessor_signature") or "",
    }
    return fields, pdf_bytes


def _moi_fields_and_pdf(data: dict) -> tuple[dict, bytes]:
    processed = process_moi_data(data)
    pdf_bytes = generate_moi_pdf(processed)
    fields = {
        "scores":              processed["scores"],
        "total_score":         processed["total_score"],
        "passed":              processed["passed"],
        "cadet_surname":       processed["cadet_surname"],
        "cadet_forename":      processed["cadet_forename"],
        "sqn_df":              processed["sqn_df"],
        "wing_ccf":            processed["wing_ccf"],
        "bader_reference":     processed["bader_reference"],
        "place_of_assessment": processed["place_of_assessment"],
        "section_comments":    processed["section_comments"],
        "strengths":           processed["strengths"],
        "improvements":        processed["improvements"],
        "general_comments":    processed["general_comments"],
        "assessor_name":       processed["assessor_name"],
        "assessor_role":       processed["assessor_role"],
        "date":                processed["date"],
        "date_iso":            data.get("date", ""),
        "assessor_signature":  processed.get("assessor_signature") or "",
        "cadet_signature":     processed.get("cadet_signature") or "",
    }
    return fields, pdf_bytes


def _validate_radio(data: dict, *, require_signature: bool) -> None:
    if not data.get("cyber_sec_date", "").strip():
        raise HTTPException(status_code=400, detail="Cyber Security video date is required.")
    if require_signature and not data.get("assessor_signature", ""):
        raise HTTPException(status_code=400, detail="Assessor signature is required.")
    if len(data.get("comments", "")) > 140:
        raise HTTPException(status_code=400, detail="Comments must be 140 characters or fewer.")


def _validate_moi(data: dict) -> None:
    for field in ("strengths_summary", "improvements_summary", "general_comments"):
        if len(data.get(field, "")) > 1150:
            raise HTTPException(status_code=400, detail=f"{field} must be 1150 characters or fewer.")
    section_comment_limits = {"identifying": 670, "delivery": 500}
    for key, val in data.get("section_comments", {}).items():
        limit = section_comment_limits.get(key, 900)
        if len(val) > limit:
            raise HTTPException(status_code=400, detail=f"Section comment '{key}' must be {limit} characters or fewer.")


# ── Creating assessments ──────────────────────────────────────────────────────

@router.post("/assessments/leadership/add-assessment")
async def generate_leadership_assessment(
    data: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    cadet = _resolve_cadet(db, data)

    assessor_name = _assessor_name(user)
    if assessor_name:
        data["assessor_name"] = assessor_name

    fields, pdf_bytes = _leadership_fields_and_pdf(data)
    sheet = _save_sheet_and_notify(db, user, cadet, "Blue Leadership", fields, pdf_bytes)
    return {"status": "success", "assessment_id": sheet.id}


@router.post("/assessments/radio/add-assessment")
async def generate_radio_assessment(
    data: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    cadet = _resolve_cadet(db, data)

    assessor_name = _assessor_name(user)
    if assessor_name:
        data["assessor_name"] = assessor_name
    data["assessor_initials"] = (
        (user.first_name or "")[:1].upper() + (user.last_name or "")[:1].upper()
    )

    _validate_radio(data, require_signature=True)

    # Pass is determined solely by whether all criteria are ticked
    criteria = data.get("criteria", {})
    data["passed"] = all(criteria.get(c) for c in criteria)

    fields, pdf_bytes = _radio_fields_and_pdf(data, cadet)
    sheet = _save_sheet_and_notify(db, user, cadet, "Blue Radio", fields, pdf_bytes)
    return {"status": "success", "assessment_id": sheet.id}


@router.post("/assessments/moi/add-assessment")
async def generate_moi_assessment(
    data: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    cadet = _resolve_cadet(db, data, allow_name_fallback=False)

    _validate_moi(data)

    assessor_name = _assessor_name(user)
    if assessor_name:
        data["assessor_name"] = assessor_name

    fields, pdf_bytes = _moi_fields_and_pdf(data)
    sheet = _save_sheet_and_notify(db, user, cadet, "MOI", fields, pdf_bytes)
    return {"status": "success", "assessment_id": sheet.id}


# ── Overview and per-sheet endpoints ──────────────────────────────────────────

@router.get("/assessments/overview")
async def assessments_overview(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    sheets = (
        db.query(AssessmentSheet)
        .join(Cadet, AssessmentSheet.cadet_id == Cadet.cin)
        .order_by(Cadet.last_name, Cadet.first_name, AssessmentSheet.created_at)
        .all()
    )

    # Group by cadet, then by assessment_type
    cadet_map: dict[int, dict] = {}
    for sheet in sheets:
        cadet = sheet.cadet
        if cadet.cin not in cadet_map:
            cadet_map[cadet.cin] = {
                "cin":        cadet.cin,
                "first_name": cadet.first_name,
                "last_name":  cadet.last_name,
                "rank":       cadet.rank,
                "flight":     cadet.flight,
                "type_map":   defaultdict(list),
            }
        cadet_map[cadet.cin]["type_map"][sheet.assessment_type].append(sheet)

    result = []
    for data in cadet_map.values():
        groups = []
        for atype, type_sheets in data["type_map"].items():
            assessments = [
                {
                    "id":              s.id,
                    "assessment_type": s.assessment_type,
                    "created_at":      s.created_at.isoformat() if s.created_at else None,
                    "passed":          s.fields.get("passed")        if s.fields else None,
                    "total_score":     s.fields.get("total_score")   if s.fields else None,
                    "exercise_name":   s.fields.get("exercise_name") if s.fields else None,
                    "assessor_name":   s.fields.get("assessor_name") if s.fields else None,
                }
                for s in type_sheets
            ]

            passed_count = sum(1 for a in assessments if a["passed"] is True)
            required = required_passes(atype)

            uploaded_at = next(
                (s.uploaded_at for s in type_sheets if s.uploaded_at),
                None,
            )
            groups.append({
                "assessment_type":    atype,
                "assessments":        assessments,
                "passed_count":       passed_count,
                "required_to_upload": required,
                "can_upload":         passed_count >= required,
                "uploaded":           any(s.uploaded for s in type_sheets),
                "uploaded_at":        uploaded_at.isoformat() if uploaded_at else None,
            })

        result.append({
            "cin":        data["cin"],
            "first_name": data["first_name"],
            "last_name":  data["last_name"],
            "rank":       data["rank"],
            "flight":     data["flight"],
            "groups":     groups,
        })

    return result


@router.get("/assessments/{assessment_id}/detail")
async def get_assessment_detail(
    assessment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Full editable data for one assessment (used to pre-fill the edit form).

    Signature blobs are stripped from the response — they are large and the
    editor does not change them; they are preserved server-side on edit.
    """
    sheet = db.query(AssessmentSheet).filter(AssessmentSheet.id == assessment_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    fields = dict(sheet.fields or {})
    fields.pop("assessor_signature", None)
    fields.pop("cadet_signature", None)

    cadet = sheet.cadet
    return {
        "id":              sheet.id,
        "assessment_type": sheet.assessment_type,
        "uploaded":        sheet.uploaded,
        "editable":        sheet.assessment_type in EDITABLE_TYPES and not sheet.uploaded,
        "cadet": {
            "cin":        cadet.cin,
            "first_name": cadet.first_name,
            "last_name":  cadet.last_name,
            "rank":       cadet.rank,
        },
        "fields": fields,
    }


@router.put("/assessments/{assessment_id}")
async def edit_assessment(
    assessment_id: int,
    data: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Edit an assessment's data and regenerate its PDF.

    Completed (uploaded) assessments are locked — they must be reopened first.
    The original assessor name and signature(s) are preserved; only the
    assessment content (scores, criteria, comments, dates, etc.) is editable.
    """
    sheet = db.query(AssessmentSheet).filter(AssessmentSheet.id == assessment_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    if sheet.uploaded:
        raise HTTPException(
            status_code=409,
            detail="This assessment is marked complete and cannot be edited. Reopen it first.",
        )

    atype = sheet.assessment_type
    if atype not in EDITABLE_TYPES:
        raise HTTPException(status_code=400, detail=f"Editing is not supported for '{atype}' assessments.")

    existing = dict(sheet.fields or {})
    cadet = sheet.cadet

    # Preserve the original assessor identity and signature — these are not
    # editable from the assessments page.
    data["assessor_name"] = existing.get("assessor_name", "")
    if not data.get("assessor_signature"):
        data["assessor_signature"] = existing.get("assessor_signature", "")

    if atype == "Blue Leadership":
        fields, pdf_bytes = _leadership_fields_and_pdf(data)
    elif atype == "Blue Radio":
        data["assessor_initials"] = existing.get("assessor_initials", "")
        _validate_radio(data, require_signature=False)
        criteria = data.get("criteria", {})
        data["passed"] = all(criteria.get(c) for c in criteria) if criteria else False
        fields, pdf_bytes = _radio_fields_and_pdf(data, cadet)
    else:  # MOI
        if not data.get("cadet_signature"):
            data["cadet_signature"] = existing.get("cadet_signature", "")
        _validate_moi(data)
        fields, pdf_bytes = _moi_fields_and_pdf(data)

    sheet.fields = fields
    sheet.pdf_data = pdf_bytes
    sheet.pdf_mime_type = "application/pdf"
    db.commit()
    db.refresh(sheet)

    return {
        "status":        "success",
        "assessment_id": sheet.id,
        "passed":        fields.get("passed"),
        "total_score":   fields.get("total_score"),
    }


@router.post("/assessments/{cin}/{assessment_type}/mark-complete")
async def mark_assessment_complete(
    cin: int,
    assessment_type: str,
    body: MarkCompleteRequest,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),  # staff override — NCOs cannot force-complete
):
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet {cin} not found.")

    sheets = [s for s in cadet.assessment_sheets if s.assessment_type == assessment_type]
    if not sheets:
        raise HTTPException(status_code=404, detail=f"No {assessment_type} assessments found for cadet {cin}.")

    now = datetime.utcnow()
    for sheet in sheets:
        sheet.uploaded = body.completed
        sheet.uploaded_at = now if body.completed else None
    db.commit()

    verb = "marked complete" if body.completed else "reopened"
    return {
        "status":  "success",
        "message": f"{assessment_type.title()} qualification {verb} for cadet {cin}.",
    }


@router.get("/assessments/{assessment_id}/pdf")
async def get_assessment_pdf(
    assessment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    sheet = db.query(AssessmentSheet).filter(AssessmentSheet.id == assessment_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    if not sheet.pdf_data:
        raise HTTPException(status_code=404, detail="No PDF stored for this assessment.")

    return StreamingResponse(
        io.BytesIO(sheet.pdf_data),
        media_type=sheet.pdf_mime_type or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="assessment_{assessment_id}.pdf"',
            "Cache-Control": "no-cache",
        },
    )


@router.delete("/assessments/{assessment_id}")
async def delete_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    sheet = db.query(AssessmentSheet).filter(AssessmentSheet.id == assessment_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    db.delete(sheet)
    db.commit()
    return {"status": "success", "message": f"Assessment {assessment_id} deleted."}


# ── Upload to Bader (runs in the global scraper slot) ─────────────────────────

@router.post("/assessments/upload-to-bader")
async def upload_qualifications_to_bader(
    data: UploadQualificationsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)

    if not user.bader_credentials:
        raise HTTPException(
            status_code=400,
            detail="Bader credentials not saved. Please go to Settings first.",
        )

    if not data.assessment_ids:
        raise HTTPException(status_code=400, detail="No assessment IDs provided.")

    # Validate all assessment IDs exist before starting
    sheets = (
        db.query(AssessmentSheet)
        .filter(AssessmentSheet.id.in_(data.assessment_ids))
        .all()
    )
    missing = set(data.assessment_ids) - {s.id for s in sheets}
    if missing:
        raise HTTPException(status_code=404, detail=f"Assessment ID(s) not found: {sorted(missing)}")

    scrapers.claim_global_scraper("upload-qualifications", idinfo.get("email"))

    bound_scraper = partial(upload_qualifications_scraper, assessment_ids=data.assessment_ids)
    background_tasks.add_task(scrapers.run_scraper_task, bound_scraper, user.id)

    return {"status": "started", "assessment_ids": data.assessment_ids}
