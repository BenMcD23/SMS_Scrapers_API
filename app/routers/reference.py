"""Reference data (uniform catalogue, badge catalogue) for the frontends."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from core.catalogue_store import reference_payload
from core.db import get_db
from core.security import require_user

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("")
def get_reference(
    response: Response,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_user),
) -> dict:
    """Everything the order and stock forms need to render their pickers.

    Any signed-in Workspace account may read it (cadets use it on the portal).
    Staff edit it on the Catalogue page, so clients may keep it only briefly:
    long enough that a page full of forms doesn't refetch it, short enough that
    a new size shows up the same evening.
    """
    response.headers["Cache-Control"] = "private, max-age=60"
    return reference_payload(db)
