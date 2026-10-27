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

from core.audit import record_audit
from core.config import COMMITTEE_EMAIL, OC_EMAIL
from core.crypto import decrypt_password
from core.db import get_db, get_or_create_user
from core.emailer import (
    committee_request_decision_html,
    committee_request_payment_html,
    committee_request_submitted_html,
    committee_request_to_committee_html,
    committee_request_withdrawn_html,
    send_email,
)
from core.http import content_disposition
from core.security import is_oc, require_oc, require_staff
from database.models import CommitteeRequest, CommitteeRequestReceipt, User
from form_generators.committee_request_pdf import build_committee_request_pdf

router = APIRouter()

RECEIPT_TYPES = ("image/png", "image/jpeg", "application/pdf")
MAX_RECEIPT_BYTES = 5 * 1024 * 1024


# ── request/response models ──────────────────────────────────────────────────

class CommitteeRequestItem(BaseModel):
    description: str   # the item's name; kept as the key old requests use
    details: str = ""  # the longer description under it: model, supplier, why this one
    quantity: int = 1
    unit_cost: float | None = None
    # The line total. Usually quantity × unit cost, but the requester can
    # override it (a multi-buy discount, delivery folded in), so it is sent
    # rather than recomputed. Old clients send only this.
    cost: float | None = None


class CommitteeRequestCreate(BaseModel):
    title: str
    justification: str = ""
    items: list[CommitteeRequestItem]


class CommitteeRequestUpdate(BaseModel):
    """The OC's edit. Every field is optional so a fix to one doesn't resend the rest."""
    title: str | None = None
    justification: str | None = None
    items: list[CommitteeRequestItem] | None = None
    rejection_reason: str | None = None


class RejectBody(BaseModel):
    reason: str = ""


# ── helpers ──────────────────────────────────────────────────────────────────

def _user_name(user: User | None) -> str:
    if not user:
        return "Unknown"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.email


def _clean_items(raw: list[CommitteeRequestItem]) -> list[dict]:
    """Validate and normalise submitted items into the stored shape. Blank rows
    are dropped; a missing unit cost or line total is filled from the other."""
    items = []
    for i in raw:
        description = i.description.strip()
        if not description:
            continue
        if i.quantity < 1:
            raise HTTPException(status_code=400, detail="Each item needs a quantity of at least 1")
        if i.unit_cost is None and i.cost is None:
            raise HTTPException(status_code=400, detail=f"{description}: give a unit cost or a total")
        unit_cost = i.unit_cost if i.unit_cost is not None else i.cost / i.quantity
        cost = i.cost if i.cost is not None else unit_cost * i.quantity
        if unit_cost < 0 or cost < 0:
            raise HTTPException(status_code=400, detail="Item costs cannot be negative")
        items.append({"description": description, "details": i.details.strip(), "quantity": i.quantity,
                      "unit_cost": round(float(unit_cost), 2), "cost": round(float(cost), 2)})
    if not items:
        raise HTTPException(status_code=400, detail="Add at least one item")
    return items


def _items_out(items: list[dict] | None) -> list[dict]:
    """Stored items in today's shape. Requests made before quantities existed
    hold only a description and cost, which reads as one of that cost."""
    out = []
    for i in items or []:
        cost = float(i.get("cost") or 0)
        quantity = i.get("quantity") or 1
        unit_cost = i.get("unit_cost")
        out.append({"description": i.get("description", ""), "details": i.get("details") or "",
                    "quantity": quantity,
                    "unit_cost": float(unit_cost) if unit_cost is not None else round(cost / quantity, 2),
                    "cost": cost})
    return out


def _build_pdf(r: CommitteeRequest) -> bytes:
    return build_committee_request_pdf(
        reference=r.reference, title=r.title, requester_name=_user_name(r.requester),
        requester_email=r.requester.email if r.requester else "", created_at=r.created_at,
        items=_items_out(r.items), total=r.total, justification=r.justification,
    )


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
        "items": _items_out(r.items),
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
def create_request(
    data: CommitteeRequestCreate,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    title = data.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="A title is required")
    items = _clean_items(data.items)
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

    if oc_inbox := OC_EMAIL.split(",")[0].strip():
        send_email(
            oc_inbox,
            f"New committee request {cr.reference}: {title}",
            committee_request_submitted_html(cr.reference, title, total, requester_name),
            attachment=cr.pdf_data, attachment_filename=f"{cr.reference}.pdf",
        )

    return _detail(cr, user, idinfo.get("email", ""))


@router.get("/committee-requests")
def list_requests(
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
def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    r = _get_or_404(db, request_id)
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.get("/committee-requests/{request_id}/pdf")
def download_request_pdf(
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


@router.patch("/committee-requests/{request_id}")
def update_request(
    request_id: int,
    data: CommitteeRequestUpdate,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """The OC corrects a request after it has gone in: the wording, the items or
    the rejection reason. The stored PDF is rebuilt so every later email and
    download carries the corrected version. A paid or withdrawn request is a
    closed record and stays as it was."""
    r = _get_or_404(db, request_id)
    if r.status in ("paid", "withdrawn"):
        raise HTTPException(status_code=409, detail="A paid or withdrawn request can no longer be edited")

    changed = []
    if data.title is not None:
        title = data.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="A title is required")
        r.title = title
        changed.append("title")
    if data.justification is not None:
        r.justification = data.justification
        changed.append("justification")
    if data.items is not None:
        r.items = _clean_items(data.items)
        r.total = round(sum(i["cost"] for i in r.items), 2)
        changed.append("items")
    if data.rejection_reason is not None:
        if r.status != "rejected":
            raise HTTPException(status_code=409, detail="Only a rejected request has a reason to edit")
        r.rejection_reason = data.rejection_reason.strip() or None
        changed.append("rejection_reason")

    if changed:
        r.pdf_data = _build_pdf(r)
        db.commit()
        record_audit(
            db, idinfo,
            action="update", resource="committee_request", resource_id=r.id,
            detail={"fields": changed},
        )
    db.refresh(r)
    user = get_or_create_user(db, idinfo)
    return _detail(r, user, idinfo.get("email", ""))


@router.post("/committee-requests/{request_id}/send-to-committee")
def send_to_committee(
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
def approve_request(
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
def reject_request(
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
def upload_receipts(
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
        content = f.file.read()
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
def get_receipt(
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
    # Receipts are any-staff readable rather than requester-scoped, so record
    # who pulled someone else's.
    record_audit(
        db, idinfo,
        action="read", resource="committee_receipt", resource_id=receipt_id,
        detail={"request_id": request_id},
    )
    return StreamingResponse(
        io.BytesIO(rec.data),
        media_type=rec.mime_type,
        # Receipt names come from the uploader's device — quotes or an accented
        # name would otherwise break the header or 500 the download.
        headers={"Content-Disposition": content_disposition("inline", rec.filename, "receipt")},
    )


@router.delete("/committee-requests/{request_id}/receipts/{receipt_id}")
def delete_receipt(
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
def send_for_payment(
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
def mark_paid(
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
def withdraw_request(
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

    if oc_inbox := OC_EMAIL.split(",")[0].strip():
        send_email(
            oc_inbox,
            f"Committee request {r.reference} withdrawn",
            committee_request_withdrawn_html(r.reference, r.title, _user_name(user)),
        )
    db.refresh(r)
    return _detail(r, user, idinfo.get("email", ""))
