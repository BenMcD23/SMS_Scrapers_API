"""Committee purchase request tracker (staff-only).

Workflow: a staff member submits a request → the OC is emailed a PDF → the OC
forwards it to the committee → after offline approval the OC approves (or rejects)
→ the requester uploads receipts → the requester sends them to the committee for
payment, with their (encrypted) bank details filled into the email.
"""

import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import CommitteeRequest, CommitteeRequestReceipt, User

from core.config import COMMITTEE_EMAIL, OC_EMAIL
from core.db import get_db, get_or_create_user
from core.emailer import (
    send_email,
    committee_request_submitted_html,
    committee_request_to_committee_html,
    committee_request_decision_html,
    committee_request_payment_html,
    committee_request_withdrawn_html,
)
from core.security import is_oc, require_oc, require_staff
from form_generators.committee_request_pdf import build_committee_request_pdf
from utils.crypto import decrypt_password

router = APIRouter()

RECEIPT_TYPES = ("image/png", "image/jpeg", "application/pdf")
MAX_RECEIPT_BYTES = 5 * 1024 * 1024


# ── request/response models ──────────────────────────────────────────────────

class CommitteeRequestItem(BaseModel):
    description: str
    cost: float


class CommitteeRequestCreate(BaseModel):
    title: str
    justification: str = ""
    items: list[CommitteeRequestItem]


class RejectBody(BaseModel):
    reason: str = ""


# ── helpers ──────────────────────────────────────────────────────────────────

def _user_name(user: User | None) -> str:
    if not user:
        return "Unknown"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.email


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _summary(r: CommitteeRequest) -> dict:
    return {
        "id": r.id,
        "reference": r.reference,
        "title": r.title,
        "total": r.total,
        "status": r.status,
        "requester_name": _user_name(r.requester),
        "requester_email": r.requester.email if r.requester else "",
        "created_at": _iso(r.created_at),
        "receipt_count": len(r.receipts),
    }


def _detail(r: CommitteeRequest, viewer: User, viewer_email: str) -> dict:
    data = _summary(r)
    data.update({
        "items": r.items or [],
        "justification": r.justification or "",
        "rejection_reason": r.rejection_reason,
        "sent_to_committee_at": _iso(r.sent_to_committee_at),
        "sent_to_committee_by": r.sent_to_committee_by,
        "decided_at": _iso(r.decided_at),
        "decided_by": r.decided_by,
        "sent_for_payment_at": _iso(r.sent_for_payment_at),
        "paid_at": _iso(r.paid_at),
        "paid_marked_by": r.paid_marked_by,
        "withdrawn_at": _iso(r.withdrawn_at),
        "withdrawn_by": r.withdrawn_by,
        "receipts": [
            {"id": rec.id, "filename": rec.filename, "mime_type": rec.mime_type,
             "uploaded_at": _iso(rec.uploaded_at)}
            for rec in r.receipts
        ],
        "can_approve": is_oc(viewer_email),
        "is_requester": r.requester_id == viewer.id,
    })
    return data


def _get_or_404(db: Session, request_id: int) -> CommitteeRequest:
    r = db.query(CommitteeRequest).filter(CommitteeRequest.id == request_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Request not found")
    return r


# ── endpoints ──────────────────────────────────────────────────────────────

@router.post("/committee-requests")
async def create_request(
    data: CommitteeRequestCreate,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    title = data.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="A title is required")
    items = [{"description": i.description.strip(), "cost": round(float(i.cost), 2)}
             for i in data.items if i.description.strip()]
    if not items:
        raise HTTPException(status_code=400, detail="Add at least one item")
    if any(i["cost"] < 0 for i in items):
        raise HTTPException(status_code=400, detail="Item costs cannot be negative")
    total = round(sum(i["cost"] for i in items), 2)

    user = get_or_create_user(db, idinfo)
    now = datetime.now()
    requester_name = _user_name(user)

    # Allocate a per-year reference. The unique (year, seq) constraint is the real
    # race guarantee — retry if a concurrent submit grabbed the same number.
    cr = None
    for _ in range(5):
        year = now.year
        max_seq = (
            db.query(func.max(CommitteeRequest.seq))
            .filter(CommitteeRequest.year == year)
            .scalar()
        ) or 0
        seq = max_seq + 1
        reference = f"CR-{year}-{seq:03d}"
        pdf = build_committee_request_pdf(
            reference=reference, title=title, requester_name=requester_name,
            requester_email=user.email, created_at=now, items=items, total=total,
            justification=data.justification,
        )
        cr = CommitteeRequest(
            year=year, seq=seq, reference=reference, title=title,
            justification=data.justification or "", items=items, total=total,
            status="submitted", pdf_data=pdf, requester_id=user.id, created_at=now,
        )
        db.add(cr)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            cr = None
    if cr is None:
        raise HTTPException(status_code=500, detail="Could not allocate a request number, please retry")
    db.refresh(cr)

    if OC_EMAIL:
        send_email(
            OC_EMAIL,
            f"New committee request {cr.reference}: {title}",
            committee_request_submitted_html(cr.reference, title, total, requester_name),
            attachment=cr.pdf_data, attachment_filename=f"{cr.reference}.pdf",
        )

    return _detail(cr, user, idinfo.get("email", ""))


@router.get("/committee-requests")
async def list_requests(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    requests = (
        db.query(CommitteeRequest)
        .order_by(CommitteeRequest.created_at.desc())
        .all()
    )
    return {
        "requests": [_summary(r) for r in requests],
        "is_oc": is_oc(idinfo.get("email", "")),
    }


@router.get("/committee-requests/{request_id}")
async def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    r = _get_or_404(db, request_id)
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.get("/committee-requests/{request_id}/pdf")
async def download_request_pdf(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    r = _get_or_404(db, request_id)
    if not r.pdf_data:
        raise HTTPException(status_code=404, detail="No PDF for this request")
    return StreamingResponse(
        io.BytesIO(r.pdf_data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{r.reference}.pdf"'},
    )


@router.post("/committee-requests/{request_id}/send-to-committee")
async def send_to_committee(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """OC approves the request and forwards it to the committee."""
    r = _get_or_404(db, request_id)
    if r.status != "submitted":
        raise HTTPException(status_code=409, detail="Request has already been sent to the committee")
    if not COMMITTEE_EMAIL:
        raise HTTPException(status_code=400, detail="COMMITTEE_EMAIL is not configured")

    send_email(
        COMMITTEE_EMAIL,
        f"Committee purchase request {r.reference}: {r.title}",
        committee_request_to_committee_html(r.reference, r.title, r.total, _user_name(r.requester)),
        attachment=r.pdf_data, attachment_filename=f"{r.reference}.pdf",
    )
    r.status = "sent_to_committee"
    r.sent_to_committee_at = datetime.now()
    r.sent_to_committee_by = idinfo.get("email", "")
    db.commit()
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    r = _get_or_404(db, request_id)
    if r.status != "sent_to_committee":
        raise HTTPException(status_code=409, detail="Request is not awaiting a decision")

    r.status = "approved"
    r.decided_at = datetime.now()
    r.decided_by = idinfo.get("email", "")
    db.commit()

    if r.requester and r.requester.email:
        send_email(
            r.requester.email,
            f"Committee request {r.reference} approved",
            committee_request_decision_html(r.reference, r.title, approved=True),
        )
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/reject")
async def reject_request(
    request_id: int,
    body: RejectBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """OC rejects — either denying at the start (submitted) or recording a
    committee rejection (sent_to_committee)."""
    r = _get_or_404(db, request_id)
    if r.status not in ("submitted", "sent_to_committee"):
        raise HTTPException(status_code=409, detail="Request is not awaiting a decision")

    r.status = "rejected"
    r.rejection_reason = (body.reason or "").strip() or None
    r.decided_at = datetime.now()
    r.decided_by = idinfo.get("email", "")
    db.commit()

    if r.requester and r.requester.email:
        send_email(
            r.requester.email,
            f"Committee request {r.reference} rejected",
            committee_request_decision_html(r.reference, r.title, approved=False,
                                            reason=r.rejection_reason),
        )
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/receipts")
async def upload_receipts(
    request_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    r = _get_or_404(db, request_id)
    user = get_or_create_user(db, idinfo)
    if r.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requester can upload receipts")
    if r.status != "approved":
        raise HTTPException(status_code=409, detail="Receipts can only be added to an approved request")

    now = datetime.now()
    for f in files:
        if f.content_type not in RECEIPT_TYPES:
            raise HTTPException(status_code=400, detail=f"{f.filename}: only PNG, JPEG or PDF receipts are accepted")
        content = await f.read()
        if len(content) > MAX_RECEIPT_BYTES:
            raise HTTPException(status_code=400, detail=f"{f.filename}: each receipt must be under 5 MB")
        db.add(CommitteeRequestReceipt(
            request_id=r.id, filename=f.filename or "receipt",
            mime_type=f.content_type, data=content,
            uploaded_at=now, uploaded_by=user.email,
        ))
    db.commit()
    db.refresh(r)
    return _detail(r, user, idinfo.get("email", ""))


@router.get("/committee-requests/{request_id}/receipts/{receipt_id}")
async def get_receipt(
    request_id: int,
    receipt_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    rec = (
        db.query(CommitteeRequestReceipt)
        .filter(CommitteeRequestReceipt.id == receipt_id,
                CommitteeRequestReceipt.request_id == request_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return StreamingResponse(
        io.BytesIO(rec.data),
        media_type=rec.mime_type,
        headers={"Content-Disposition": f'inline; filename="{rec.filename}"'},
    )


@router.delete("/committee-requests/{request_id}/receipts/{receipt_id}")
async def delete_receipt(
    request_id: int,
    receipt_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    r = _get_or_404(db, request_id)
    user = get_or_create_user(db, idinfo)
    if r.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requester can delete receipts")
    if r.status != "approved":
        raise HTTPException(status_code=409, detail="Receipts can no longer be changed")
    rec = (
        db.query(CommitteeRequestReceipt)
        .filter(CommitteeRequestReceipt.id == receipt_id,
                CommitteeRequestReceipt.request_id == request_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Receipt not found")
    db.delete(rec)
    db.commit()
    db.refresh(r)
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/send-for-payment")
async def send_for_payment(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    r = _get_or_404(db, request_id)
    user = get_or_create_user(db, idinfo)
    if r.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requester can send for payment")
    if r.status != "approved":
        raise HTTPException(status_code=409, detail="Request must be approved before sending for payment")
    if not r.receipts:
        raise HTTPException(status_code=400, detail="Upload at least one receipt first")
    if not COMMITTEE_EMAIL:
        raise HTTPException(status_code=400, detail="COMMITTEE_EMAIL is not configured")

    profile = user.profile
    account_name = decrypt_password(profile.bank_account_name) if profile and profile.bank_account_name else None
    sort_code = decrypt_password(profile.bank_sort_code) if profile and profile.bank_sort_code else None
    account_number = decrypt_password(profile.bank_account_number) if profile and profile.bank_account_number else None
    if not (account_name and sort_code and account_number):
        raise HTTPException(status_code=400, detail="Add your bank details in Settings before sending for payment")

    attachments = [(rec.filename, rec.data, rec.mime_type) for rec in r.receipts]
    if r.pdf_data:
        attachments.append((f"{r.reference}.pdf", r.pdf_data, "application/pdf"))

    send_email(
        COMMITTEE_EMAIL,
        f"Payment request {r.reference}: {r.title}",
        committee_request_payment_html(
            r.reference, r.title, r.total, _user_name(user),
            account_name, sort_code, account_number, len(r.receipts),
        ),
        attachments=attachments,
    )
    r.status = "sent_for_payment"
    r.sent_for_payment_at = datetime.now()
    db.commit()
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/mark-paid")
async def mark_paid(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    r = _get_or_404(db, request_id)
    if r.status != "sent_for_payment":
        raise HTTPException(status_code=409, detail="Request is not awaiting payment")
    r.status = "paid"
    r.paid_at = datetime.now()
    r.paid_marked_by = idinfo.get("email", "")
    db.commit()
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/withdraw")
async def withdraw_request(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The requester withdraws their own request. Allowed at any point except
    once it has been paid (or already withdrawn)."""
    r = _get_or_404(db, request_id)
    user = get_or_create_user(db, idinfo)
    if r.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requester can withdraw this request")
    if r.status in ("paid", "withdrawn"):
        raise HTTPException(status_code=409, detail="This request can no longer be withdrawn")

    r.status = "withdrawn"
    r.withdrawn_at = datetime.now()
    r.withdrawn_by = user.email
    db.commit()

    if OC_EMAIL:
        send_email(
            OC_EMAIL,
            f"Committee request {r.reference} withdrawn",
            committee_request_withdrawn_html(r.reference, r.title, _user_name(user)),
        )
    db.refresh(r)
    return _detail(r, user, idinfo.get("email", ""))
