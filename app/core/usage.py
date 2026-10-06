"""Feature-usage counting — which endpoints get called, by whom, how often.

Every successful request is folded into a per-day counter (Usage_Daily) by the
middleware in api.py, so the owner can see what is actually used and what never
is (GET /usage). Counting lives here, in one place, rather than in each router.
"""

import logging
from contextvars import ContextVar
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from database.database import SessionLocal
from database.models import UsageDaily

logger = logging.getLogger(__name__)

# Not worth counting: probes, the auth check the site polls, and the usage page
# itself (looking at the numbers shouldn't inflate them).
SKIP_ROUTES = {"/ping", "/healthz", "/readyz", "/health", "/usage"}

# The caller's email, filled in by the auth dependencies. A mutable holder rather
# than the email itself: dependencies run in a worker thread with a *copy* of
# this context, so only a change made inside a shared object gets back to the
# middleware that reads it.
_caller: ContextVar[dict | None] = ContextVar("usage_caller", default=None)


def start_request() -> dict:
    holder: dict = {}
    _caller.set(holder)
    return holder


def note_caller(email: str) -> None:
    holder = _caller.get()
    if holder is not None:
        holder["email"] = (email or "").lower()


def record(method: str, route: str, email: str, now: datetime | None = None) -> None:
    """Add one call to today's counter. Never raises: losing a count is better
    than failing the request it describes."""
    now = now or datetime.now()
    try:
        for _ in range(2):
            db = SessionLocal()
            try:
                key = dict(day=now.date(), method=method, route=route, email=email)
                row = db.query(UsageDaily).filter_by(**key).first()
                if row:
                    row.calls += 1
                    row.last_at = now
                else:
                    db.add(UsageDaily(**key, calls=1, last_at=now))
                db.commit()
                return
            except IntegrityError:
                # Another request created today's row between our read and
                # insert — go round once more and increment it instead.
                db.rollback()
            finally:
                db.close()
    except Exception as e:
        logger.warning(f"usage count failed for {method} {route}: {e}")
