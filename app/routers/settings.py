"""User settings — Bader credentials, signature image, profile details."""

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.crypto import decrypt_password, encrypt_password
from core.db import get_db, get_or_create_user
from core.security import require_staff, require_staff_or_nco
from database.models import BaderCredentials, Cadet, Staff, UserProfile, UserSignature
from texts.phone import clean_mobile
from texts.settings import community_invite_url

router = APIRouter()


class UserProfilePatch(BaseModel):
    # Fixed fields
    rank:        str | None = None
    initials:    str | None = None
    surname:     str | None = None
    jpa_number:  str | None = None
    appointment: str | None = None
    sqn_vgs_no:  str | None = None
    wing_ccf:    str | None = None
    # Editable fields
    home_address: str | None = None
    car_reg:      str | None = None
    # Bank details for committee-request reimbursements (encrypted at rest)
    bank_account_name:   str | None = None
    bank_sort_code:      str | None = None
    bank_account_number: str | None = None
    # User table fields
    first_name: str | None = None
    last_name:  str | None = None


class AssessorNamePatch(BaseModel):
    assessor_name: str


class PhoneNumberPatch(BaseModel):
    phone_number: str


@router.post("/save-credentials")
def save_credentials(
    data: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)

    creds = user.bader_credentials
    if not creds:
        creds = BaderCredentials(user_id=user.id)
        db.add(creds)

    creds.role_username = data.get("role_user")
    creds.role_password = encrypt_password(data.get("role_pass"))
    creds.personal_username = data.get("pers_user")
    creds.personal_password = encrypt_password(data.get("pers_pass"))

    db.commit()
    return {"status": "success", "message": f"Settings saved for {user.email}"}


@router.post("/save-signature")
async def save_signature(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)

    if file.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(status_code=400, detail="Only PNG or JPEG images are accepted")

    image_bytes = await file.read()
    if len(image_bytes) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Signature image must be under 2 MB")

    sig = user.signature
    if not sig:
        sig = UserSignature(user_id=user.id)
        db.add(sig)

    sig.image_data = image_bytes
    sig.mime_type = file.content_type

    db.commit()
    return {"status": "success", "message": "Signature saved"}


@router.get("/get-signature")
def get_signature(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    if not user.signature:
        raise HTTPException(status_code=404, detail="No signature saved")

    return StreamingResponse(
        io.BytesIO(user.signature.image_data),
        media_type=user.signature.mime_type,
        headers={"Cache-Control": "no-cache"},
    )


@router.delete("/delete-signature")
def delete_signature(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    if not user.signature:
        raise HTTPException(status_code=404, detail="No signature to delete")

    db.delete(user.signature)
    db.commit()
    return {"status": "success", "message": "Signature deleted"}


@router.get("/settings/user-profile")
def get_user_profile(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    p = user.profile
    return {
        "first_name":   user.first_name or "",
        "last_name":    user.last_name or "",
        "rank":         p.rank        if p else "",
        "initials":     p.initials    if p else "",
        "surname":      p.surname     if p else "",
        "jpa_number":   p.jpa_number  if p else "",
        "appointment":  p.appointment if p else "",
        "sqn_vgs_no":   p.sqn_vgs_no  if p else "",
        "wing_ccf":     p.wing_ccf    if p else "",
        "home_address": p.home_address if p else "",
        "car_reg":      p.car_reg      if p else "",
        # Decrypted for the owner's own view only — never surfaced elsewhere.
        "bank_account_name":   (decrypt_password(p.bank_account_name)   if p and p.bank_account_name   else "") or "",
        "bank_sort_code":      (decrypt_password(p.bank_sort_code)      if p and p.bank_sort_code      else "") or "",
        "bank_account_number": (decrypt_password(p.bank_account_number) if p and p.bank_account_number else "") or "",
    }


@router.patch("/settings/user-profile")
def update_user_profile(
    data: UserProfilePatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)

    if data.first_name is not None:
        user.first_name = data.first_name.strip()
    if data.last_name is not None:
        user.last_name = data.last_name.strip()

    p = user.profile
    if not p:
        p = UserProfile(user_id=user.id)
        db.add(p)

    for field in ("rank", "initials", "surname", "jpa_number", "appointment",
                  "sqn_vgs_no", "wing_ccf", "home_address", "car_reg"):
        val = getattr(data, field)
        if val is not None:
            setattr(p, field, val.strip())

    # Bank details are encrypted at rest; an empty string clears the field.
    for field in ("bank_account_name", "bank_sort_code", "bank_account_number"):
        val = getattr(data, field)
        if val is not None:
            cleaned = val.strip()
            setattr(p, field, encrypt_password(cleaned) if cleaned else None)

    db.commit()
    return {"status": "success"}


@router.get("/settings/assessor-name")
def get_assessor_name(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    p = user.profile
    return {"assessor_name": p.assessor_name if p else ""}


@router.patch("/settings/assessor-name")
def update_assessor_name(
    data: AssessorNamePatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)

    p = user.profile
    if not p:
        p = UserProfile(user_id=user.id)
        db.add(p)

    p.assessor_name = data.assessor_name.strip()
    db.commit()
    return {"status": "success"}


# ── Parade-night text number ──────────────────────────────────────────────────
#
# The number lives on the squadron record the signed-in account belongs to —
# the Staff row for a CFAV, the Cadet row for an NCO — not on User, so the text
# list can read a rank and a surname off the same row and greet people properly.


def _phone_owner(db: Session, email: str):
    """The roster row this account's number belongs on, or (None, None) if the
    account isn't on either roster — a new CFAV before the first scrape, say."""
    email = (email or "").strip().lower()
    if not email:
        return None, None

    staff = db.query(Staff).filter(func.lower(Staff.email) == email).first()
    if staff:
        return "staff", staff

    cadet = db.query(Cadet).filter(func.lower(Cadet.email) == email).first()
    if cadet:
        return "cadet", cadet

    return None, None


def _phone_json(db: Session, kind: str | None, row) -> dict:
    return {
        "phone_number": (row.phone_number or "") if row else "",
        # Which roster it's stored on, so the UI can say "as a cadet" / "as
        # staff" — and None when there's nowhere to store it at all.
        "kind": kind,
        "name": f"{row.first_name} {row.last_name}".strip() if row else "",
        # Sent alongside the number so the page can offer the community as soon
        # as one is saved, without a second round trip.
        "whatsapp_invite_url": community_invite_url(db),
    }


@router.get("/settings/phone-number")
def get_phone_number(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    kind, row = _phone_owner(db, idinfo.get("email", ""))
    return _phone_json(db, kind, row)


@router.patch("/settings/phone-number")
def update_phone_number(
    data: PhoneNumberPatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Save the mobile that gets the parade-night texts. An empty value clears
    it, which is how somebody takes themselves off the list."""
    kind, row = _phone_owner(db, idinfo.get("email", ""))
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Your account isn't linked to a squadron record yet, so there's nowhere to save a number.",
        )

    try:
        phone = clean_mobile(data.phone_number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    row.phone_number = phone or None
    db.commit()
    return {"status": "success", **_phone_json(db, kind, row)}
