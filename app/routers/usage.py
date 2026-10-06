"""Owner-only feature-usage report.

Reads the per-day counters core/usage.py keeps, so the owner can see which
endpoints people actually call, who calls them, and which are never called at
all — the evidence for deciding what to keep, improve or delete.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from core.db import get_db
from core.security import get_roles_for_emails, require_owner
from core.usage import SKIP_ROUTES
from database.database import SessionLocal
from database.models import UsageDaily

logger = logging.getLogger(__name__)

router = APIRouter(tags=["usage"])

# Long enough to see termly features (assessments, inspections) come round.
USAGE_RETENTION_DAYS = 180


def _role(email: str, roles: dict[str, str | None]) -> str:
    if not email:
        return "anonymous"
    # Anyone in the Workspace outside the staff/SNCO/NCO groups is a cadet —
    # require_user lets them into the portal endpoints.
    return roles.get(email) or "cadet"


@router.get("/usage")
def get_usage(
    request: Request,
    days: int = Query(30, ge=1, le=USAGE_RETENTION_DAYS),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_owner),
):
    since = date.today() - timedelta(days=days - 1)
    rows = db.query(UsageDaily).filter(UsageDaily.day >= since).all()
    roles = get_roles_for_emails(sorted({r.email for r in rows if r.email}))

    routes: dict[tuple[str, str], dict] = {}
    users: dict[str, dict] = {}
    for r in rows:
        role = _role(r.email, roles)
        rt = routes.setdefault((r.method, r.route), {
            "method": r.method, "route": r.route, "calls": 0, "users": set(),
            "roles": defaultdict(int), "last_used": r.last_at,
        })
        rt["calls"] += r.calls
        rt["roles"][role] += r.calls
        rt["last_used"] = max(rt["last_used"], r.last_at)
        if r.email:
            rt["users"].add(r.email)
            u = users.setdefault(r.email, {
                "email": r.email, "role": role, "calls": 0, "routes": set(), "last_used": r.last_at,
            })
            u["calls"] += r.calls
            u["routes"].add((r.method, r.route))
            u["last_used"] = max(u["last_used"], r.last_at)

    # Every endpoint the app serves, so the ones nobody called show up too. Read
    # from the OpenAPI schema — the public, cached list of routes and methods.
    served = {
        (m.upper(), path)
        for path, ops in request.app.openapi()["paths"].items()
        if path not in SKIP_ROUTES
        for m in ops
        if m.upper() not in ("HEAD", "OPTIONS")
    }

    return {
        "days": days,
        "since": since.isoformat(),
        "total_calls": sum(r.calls for r in rows),
        "routes": sorted(
            ({**rt, "users": len(rt["users"]), "roles": dict(rt["roles"]),
              "last_used": rt["last_used"].isoformat()} for rt in routes.values()),
            key=lambda rt: (-rt["calls"], rt["route"], rt["method"]),
        ),
        "users": sorted(
            ({**u, "routes": len(u["routes"]), "last_used": u["last_used"].isoformat()} for u in users.values()),
            key=lambda u: (-u["calls"], u["email"]),
        ),
        "unused": [{"method": m, "route": p} for p, m in sorted((p, m) for m, p in served - routes.keys())],
    }


def cleanup_old_usage():
    cutoff = (datetime.now() - timedelta(days=USAGE_RETENTION_DAYS)).date()
    db = SessionLocal()
    try:
        deleted = db.query(UsageDaily).filter(UsageDaily.day < cutoff).delete(synchronize_session=False)
        db.commit()
        if deleted:
            logger.info(f"usage cleanup purged {deleted} counter(s) older than {USAGE_RETENTION_DAYS} days")
    except Exception as e:
        db.rollback()
        logger.error(f"usage cleanup failed: {e}")
    finally:
        db.close()
