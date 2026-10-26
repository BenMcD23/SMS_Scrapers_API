"""Feature-usage counting — which endpoints get called, by whom, how often.

Every successful request is folded into a per-day counter (Usage_Daily) by the
middleware in api.py, so the owner can see what is actually used and what never
is (GET /usage). Counts are batched in memory and flushed every half minute, so
counting costs a request nothing. Counting lives here, in one place, rather than in each router.
"""

import logging
import threading
from contextvars import ContextVar
from datetime import datetime

from sqlalchemy import func

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


# Counts not yet written: (day, method, route, email) -> [calls, last_at]. A
# request only bumps this; flush() folds it into Usage_Daily every
# FLUSH_INTERVAL_S, so counting never adds a database round trip to a request.
_pending: dict[tuple, list] = {}
_pending_lock = threading.Lock()
FLUSH_INTERVAL_S = 30
_stop = threading.Event()
_thread: threading.Thread | None = None


def record(method: str, route: str, email: str, now: datetime | None = None) -> None:
    """Add one call to today's counter, in memory. Never raises: losing a
    count is better than failing the request it describes."""
    now = now or datetime.now()
    key = (now.date(), method, route, email)
    with _pending_lock:
        entry = _pending.get(key)
        if entry:
            entry[0] += 1
            entry[1] = max(entry[1], now)
        else:
            _pending[key] = [1, now]


def _upsert(db, values: dict):
    """INSERT ... ON CONFLICT DO UPDATE that adds to the existing row, so two
    processes flushing the same key both land (no read-then-write race)."""
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        latest = func.greatest
    else:
        from sqlalchemy.dialects.sqlite import insert
        latest = func.max  # SQLite's two-argument max() is the scalar one
    stmt = insert(UsageDaily).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=["day", "method", "route", "email"],
        set_={
            "calls": UsageDaily.calls + stmt.excluded.calls,
            "last_at": latest(UsageDaily.last_at, stmt.excluded.last_at),
        },
    )


def flush() -> int:
    """Write the pending counts to Usage_Daily; returns how many keys were
    written. On failure the counts go back into the pending map to be retried
    next time, so a database blip delays them rather than losing them."""
    with _pending_lock:
        batch = dict(_pending)
        _pending.clear()
    if not batch:
        return 0
    try:
        with SessionLocal() as db:
            for (day, method, route, email), (calls, last_at) in batch.items():
                db.execute(_upsert(db, dict(day=day, method=method, route=route, email=email,
                                            calls=calls, last_at=last_at)))
            db.commit()
        return len(batch)
    except Exception as e:
        logger.warning(f"usage flush failed, keeping {len(batch)} counter(s) for next time: {e}")
        with _pending_lock:
            for key, (calls, last_at) in batch.items():
                entry = _pending.setdefault(key, [0, last_at])
                entry[0] += calls
                entry[1] = max(entry[1], last_at)
        return 0


def _run() -> None:
    while not _stop.wait(FLUSH_INTERVAL_S):
        flush()


def start_flusher() -> None:
    """Flush in the background for as long as the process runs. Every process
    runs its own (each holds its own counts), unlike the leader-only scheduler."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="usage-flush", daemon=True)
    _thread.start()


def stop_flusher() -> None:
    """Stop the background flush and write what's left, so a deploy's
    shutdown doesn't drop the last half-minute of counts."""
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
    flush()
