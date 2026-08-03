"""Recording side of the audit trail.

Domain tables already stamp their own workflows (ScraperRun.ran_by,
CommitteeRequest.decided_by, StoresItemIssuance.given_by). This covers what
those stamps can't: who *read* special-category data, who deleted something,
and what left the system.

Call record_audit() from inside a handler once the action has actually
happened, so the trail reflects what occurred rather than what was attempted.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from database.models import AuditEvent
from core.security import get_user_role


def record_audit(
    db: Session,
    idinfo: dict,
    *,
    action: str,
    resource: str,
    resource_id=None,
    detail: dict | None = None,
) -> None:
    """Write one audit row for the caller behind `idinfo`.

    Committed on its own rather than left for the caller's commit, so the read
    handlers — which have no commit of their own — are covered by the same call
    as the write handlers.

    A failure here is swallowed: losing an audit row is bad, but taking down a
    medical-records page because the audit insert failed is worse. The rollback
    matters — without it the caller's session is left unusable.
    """
    email = idinfo.get("email", "")
    try:
        db.add(AuditEvent(
            occurred_at=datetime.now(),
            actor_email=email,
            # Cached for 5 minutes by get_user_role, so this is not a per-request
            # round-trip to the Google admin API.
            actor_role=get_user_role(email),
            action=action,
            resource=resource,
            resource_id=None if resource_id is None else str(resource_id),
            detail=detail,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[record_audit] failed to record {action} on {resource}: {e}")
