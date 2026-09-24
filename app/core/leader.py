"""Run the background scheduler in exactly one process.

Every API process starts its scheduler paused. Whichever one holds a Postgres
advisory lock resumes it; the rest keep retrying. That's what makes a rolling
deploy safe: the new pod serves requests at once but only takes over the jobs
when the old pod exits and its lock goes with it.

The lock lives on a dedicated connection, so Postgres releases it the moment
the holder's process dies or its connection drops - including a database
failover, since the new primary never had it. No lease or expiry to tune.
TCP keepalives and tcp_user_timeout bound how long a silently dead
connection (a partitioned node) can go unnoticed.

SQLite (tests, local without Postgres) has no advisory locks, so the
scheduler just runs, as it always did.
"""

import logging
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from database.database import DATABASE_URL

logger = logging.getLogger(__name__)

LOCK_ID = 317_000_001  # arbitrary; only has to be unique within this database
POLL_SECONDS = 5

_stop = threading.Event()
_thread: threading.Thread | None = None


def start(scheduler, on_acquire) -> None:
    """Start `scheduler`, running jobs only while this process holds the lock.

    `on_acquire` runs each time the lock is taken, before jobs resume - for
    re-reading anything another process may have changed in the meantime.
    """
    global _thread
    if not DATABASE_URL.startswith("postgresql"):
        scheduler.start()
        logger.info("background scheduler started (no Postgres, so no leader lock)")
        return

    scheduler.start(paused=True)
    _stop.clear()
    _thread = threading.Thread(target=_hold_lock, args=(scheduler, on_acquire), name="scheduler-leader", daemon=True)
    _thread.start()


def stop() -> None:
    """Stop trying for the lock and release it (closing its connection)."""
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=POLL_SECONDS + 5)


def _hold_lock(scheduler, on_acquire) -> None:
    lock_engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "keepalives": 1,
            "keepalives_idle": 5,
            "keepalives_interval": 2,
            "keepalives_count": 3,
            "tcp_user_timeout": 15000,  # ms
        },
    )
    conn = None
    leader = False
    wait = 0.0
    while not _stop.wait(wait):
        wait = POLL_SECONDS
        try:
            if conn is None:
                # AUTOCOMMIT: a session-level lock needs no transaction, and an
                # idle-in-transaction connection held forever would stall vacuum.
                conn = lock_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            if leader:
                conn.execute(text("SELECT 1"))  # connection alive means lock still held
            elif conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID}).scalar():
                on_acquire()
                scheduler.resume()
                leader = True
                logger.info("scheduler leader lock acquired; background jobs running in this process")
        except Exception as e:
            logger.warning(f"scheduler leader lock: {e}")
            if leader:
                scheduler.pause()
                leader = False
                logger.warning("scheduler leader lock lost; background jobs paused")
            if conn is not None:
                try:
                    conn.invalidate()
                except Exception:
                    pass
                conn = None
    if leader:
        scheduler.pause()
    if conn is not None:
        conn.close()
    lock_engine.dispose()
