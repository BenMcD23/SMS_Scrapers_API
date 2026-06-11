"""DB session dependency and user lookup helpers."""

from fastapi import Depends, HTTPException, Header
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import User, Cadet

from core.security import require_user


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_or_create_user(db: Session, idinfo: dict) -> User:
    """Fetch the User row for a verified token, creating it on first sight."""
    google_id = idinfo["sub"]
    first_name = idinfo.get("given_name")
    last_name = idinfo.get("family_name")

    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        user = User(google_id=google_id, email=idinfo["email"], first_name=first_name, last_name=last_name)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if first_name is not None and user.first_name != first_name:
            user.first_name = first_name
        if last_name is not None and user.last_name != last_name:
            user.last_name = last_name
        db.commit()
    return user


def get_current_user(
    idinfo: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> User:
    return get_or_create_user(db, idinfo)


def get_current_cadet(
    idinfo: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> Cadet:
    """Resolve a token to a Cadet by email — used by the cadet portal."""
    email = idinfo.get("email", "")
    cadet = db.query(Cadet).filter(func.lower(Cadet.email) == email.lower()).first()
    if not cadet:
        raise HTTPException(
            status_code=404,
            detail="You are not registered in the system. Please speak to a member of staff.",
        )
    return cadet
