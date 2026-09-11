"""Reference data (uniform catalogue, badge catalogue) for the frontends."""

from fastapi import APIRouter, Depends, Response

from core.catalogue import reference_payload
from core.security import require_user

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("")
def get_reference(response: Response, idinfo: dict = Depends(require_user)) -> dict:
    """Everything the order and stock forms need to render their pickers.

    Any signed-in Workspace account may read it (cadets use it on the portal).
    It changes only with a deploy, so clients are told to cache it for an hour.
    """
    response.headers["Cache-Control"] = "private, max-age=3600"
    return reference_payload()
