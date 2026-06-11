"""User settings — Bader credentials, signature image, profile details."""

import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.models import BaderCredentials, User, UserProfile, UserSignature

from core.db import get_db, get_or_create_user
from core.security import require_staff, require_staff_or_nco
from utils.crypto import encrypt_password

router = APIRouter()


class UserProfilePatch(BaseModel):
    # Fixed fields
    rank:        Optional[str] = None
    initials:    Optional[str] = None
    surname:     Optional[str] = None
    jpa_number:  Optional[str] = None
    appointment: Optional[str] = None
    sqn_vgs_no:  Optional[str] = None
    wing_ccf:    Optional[str] = None
    # Editable fields
    home_address: Optional[str] = None
    car_reg:      Optional[str] = None
    # User table fields
    first_name: Optional[str] = None
    last_name:  Optional[str] = None


class AssessorNamePatch(BaseModel):
    assessor_name: str


@router.post("/save-credentials")
async def save_credentials(
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
async def get_signature(
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
async def delete_signature(
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
async def get_user_profile(
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
    }


@router.patch("/settings/user-profile")
async def update_user_profile(
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

    db.commit()
    return {"status": "success"}


@router.get("/settings/assessor-name")
async def get_assessor_name(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    user = get_or_create_user(db, idinfo)
    p = user.profile
    return {"assessor_name": p.assessor_name if p else ""}


@router.patch("/settings/assessor-name")
async def update_assessor_name(
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
