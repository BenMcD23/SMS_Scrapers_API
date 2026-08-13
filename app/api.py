"""App entrypoint — wires up middleware, background jobs, and the routers.

Endpoint logic lives in routers/, shared helpers in core/.
"""

import os
from contextlib import asynccontextmanager

from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from database.database import IS_LAMBDA

from core import jobs
from core.scheduler import scheduler
from core.security import require_user
from routers import (
    assessments, attendance, backups, badges, cadets, committee, events,
    form_generators, inspections, leaving, nco_appraisals, nco_holidays, newsletters,
    oc, portal, programme, scrapers, session_plans, settings, stats, stores, texts, nco_comments
)

# On Lambda the scheduled jobs are EventBridge rules dispatched by
# lambda_handler.py — a frozen container can't hold a scheduler. Everywhere
# else (local dev, and the home stack while the database move is being
# de-risked ahead of the Lambda cutover) they still run in-process, unchanged.
# Set ENABLE_LOCAL_SCHEDULER=false to silence them on a home stack that is
# running alongside a live Lambda, so nothing fires twice.
ENABLE_LOCAL_SCHEDULER = (
    not IS_LAMBDA and os.getenv("ENABLE_LOCAL_SCHEDULER", "true").lower() != "false"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed exclusively by Alembic migrations (run in CI against
    # Neon's direct URL on deploy), not create_all — see README "Database
    # Migrations".
    if not ENABLE_LOCAL_SCHEDULER:
        yield
        return

    scheduler.add_job(jobs.cleanup_old_completed_orders, "interval", hours=24)
    scheduler.add_job(jobs.cleanup_old_completed_assessments, "interval", hours=24)
    scheduler.add_job(jobs.cleanup_old_run_logs, "interval", hours=24)
    # Friday alert for qualifications now within 3 months of expiry — each
    # cadet+qualification is emailed once (deduped via expiry_alert_sent_at).
    scheduler.add_job(
        jobs.quali_expiry_alert,
        CronTrigger(day_of_week="fri", hour=7, minute=0, timezone="Europe/London"),
    )
    # 4pm Tue/Thu — sends the ready parade-night text for the next day (Wed/Fri)
    scheduler.add_job(
        jobs.scheduled_send_job,
        CronTrigger(day_of_week="tue,thu", hour=16, minute=0, timezone="Europe/London"),
    )
    # Scraper schedules are NOT registered here — they belong to the worker, so
    # they keep firing when this process is unreachable.
    scheduler.add_job(
        jobs.db_backup,
        CronTrigger(hour=3, minute=0, timezone="Europe/London"),
        id="db_backup",
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

# Compress larger JSON payloads (cadet lists, stats, stores) — the home link is
# the bottleneck, so shrinking the body cuts transfer time noticeably.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow the Next.js frontends to talk to us. localhost is only allowed when
# CORS_ALLOW_LOCALHOST is set (dev), never in prod.
_cors_origins = ["https://sms.317atc.co.uk", "https://317-sms-site.vercel.app"]
if os.getenv("CORS_ALLOW_LOCALHOST", "").lower() == "true":
    _cors_origins.append("http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Anchored to Vercel preview deploys of exactly this project — an unanchored
    # ".*" would also match attacker-registered 317-sms-site-*.vercel.app origins.
    allow_origin_regex=r"^https://317-sms-site-[a-z0-9-]+\.vercel\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    allow_credentials=True,
)


@app.get("/ping")
def ping():
    """Unauthenticated liveness probe — polled by the frontend's API-down
    overlay. Returns nothing sensitive, just proof the API is reachable."""
    return {"ok": True}


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
