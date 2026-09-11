"""App entrypoint — wires up middleware, background jobs, and the routers.

Endpoint logic lives in routers/, shared helpers in core/, scheduled work in
core/jobs.py.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import text

from core.config import CORS_ORIGIN_REGEX, CORS_ORIGINS, SCHEDULER_ENABLED
from core.jobs import register_jobs
from core.logging import configure_logging
from core.scheduler import scheduler
from core.security import require_user
from database.database import engine
from routers import (
    assessments,
    attendance,
    backups,
    badges,
    cadets,
    committee,
    events,
    form_generators,
    inspections,
    leaving,
    nco_appraisals,
    nco_comments,
    nco_holidays,
    newsletters,
    oc,
    portal,
    programme,
    reference,
    scrapers,
    session_plans,
    settings,
    stats,
    stores,
    texts,
)

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed exclusively by Alembic migrations, run before the app
    # starts (deploy job / compose step), never by create_all here.
    if SCHEDULER_ENABLED:
        register_jobs(scheduler)
        scheduler.start()
        logger.info("background scheduler started")
    else:
        logger.info("background scheduler disabled on this replica")
    yield
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

# Compress larger JSON payloads (cadet lists, stats, stores) — the home link is
# the bottleneck, so shrinking the body cuts transfer time noticeably.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow the Next.js frontends to talk to us (origins come from config/env).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    allow_credentials=True,
)


# ── Probes ────────────────────────────────────────────────────────────────────
# /ping and /healthz answer as soon as the process is up (liveness). /readyz
# also checks the database, so the container is only reported healthy once the
# app can actually serve. /health is the authenticated check the SMS site uses to
# confirm its token is accepted.


@app.get("/ping", include_in_schema=False)
def ping():
    """Unauthenticated liveness probe — polled by the frontends' API-down overlay."""
    return {"ok": True}


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/readyz", include_in_schema=False)
def readyz(response: Response):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # pragma: no cover - exercised against a real DB
        logger.warning(f"readiness check failed: {e}")
        response.status_code = 503
        return {"ok": False, "database": "unreachable"}
    return {"ok": True, "database": "ok"}


@app.get("/health")
def health_check(idinfo: dict = Depends(require_user)):
    return {"ok": True, "email": idinfo["email"]}


# portal must come before cadets so /cadets/me isn't swallowed by /cadets/{cin}
app.include_router(portal.router)
app.include_router(cadets.router)
app.include_router(inspections.router)
app.include_router(scrapers.router)
app.include_router(settings.router)
app.include_router(form_generators.router)
app.include_router(events.router)
app.include_router(programme.router)
app.include_router(newsletters.router)
app.include_router(assessments.router)
app.include_router(stats.router)
app.include_router(stores.router)
app.include_router(badges.router)
app.include_router(texts.router)
app.include_router(backups.router)
app.include_router(committee.router)
app.include_router(oc.router)
app.include_router(session_plans.router)
app.include_router(nco_holidays.router)
app.include_router(nco_appraisals.router)
app.include_router(nco_comments.router)
app.include_router(attendance.router)
app.include_router(leaving.router)
app.include_router(reference.router)
