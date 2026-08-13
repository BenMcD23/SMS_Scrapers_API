"""Bader scrapers — queueing, log polling, start/stop.

Nothing in this module runs a scraper. The API lives in the cloud and the
browser automation lives on the home box, so a run is a row in `Scraper_Jobs`
that the worker (app/worker.py) claims over an outbound connection. This module
only writes and reads those rows, which is why it must stay free of Playwright
and of anything that imports it — see scripts/scraper_calls.py.

Named scrapers (cadet-quali, cadet-event, 317-event, medical, staff, absences)
allow one live job each, enforced by a partial unique index rather than by
in-process state. Upload jobs are exempt: any number can be queued at once.
"""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import (
    ACTIVE_JOB_STATUSES, AttachmentCheckQual, ScraperJob, ScraperJobLog,
    ScraperRun, ScraperSchedule,
)

from core.db import get_db, get_or_create_user
from core.security import require_staff, require_owner

router = APIRouter()

RUN_LOG_RETENTION_DAYS = 7

NAMED_SCRAPERS = ["cadet-quali", "cadet-event", "317-event", "medical", "staff", "absences"]

# Upload-to-Bader jobs share the queue with the named scrapers but not their
# one-at-a-time rule, so they're identified by this reserved scraper_id.
UPLOAD_SCRAPER_ID = "upload-qualifications"


# ── Queue helpers ─────────────────────────────────────────────────────────────

def enqueue_job(
    db: Session,
    scraper_id: str,
    requested_by: str | None,
    payload: dict | None = None,
) -> ScraperJob:
    """Queue a run, or 409 if this named scraper already has a live job.

    The uniqueness check is the partial unique index, not a prior SELECT: two
    Lambda containers can serve two clicks at the same instant, and only the
    database sees both.
    """
    job = ScraperJob(
        scraper_id=scraper_id,
        status="queued",
        payload=payload,
        requested_by=requested_by,
        requested_at=datetime.now(),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Scraper already running")
    db.refresh(job)
    return job


def active_job(db: Session, scraper_id: str) -> ScraperJob | None:
    return (
        db.query(ScraperJob)
        .filter(
            ScraperJob.scraper_id == scraper_id,
            ScraperJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(ScraperJob.requested_at.desc())
        .first()
    )


def _request_stop(db: Session, job: ScraperJob) -> str:
    """Flag a job to stop. A job the worker hasn't claimed yet is cancelled
    outright — otherwise it would sit queued until a worker picked it up just
    to stop immediately, and the scraper slot would stay locked meanwhile."""
    job.stop_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = datetime.now()
        db.commit()
        return "cancelled"
    db.commit()
    return "stopping"


def _job_json(job: ScraperJob) -> dict:
    return {
        "job_id":       job.id,
        "scraper_id":   job.scraper_id,
        "status":       job.status,
        "running":      job.status in ACTIVE_JOB_STATUSES,
        "started_by":   job.requested_by,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "started_at":   job.claimed_at.isoformat() if job.claimed_at else None,
        "finished_at":  job.finished_at.isoformat() if job.finished_at else None,
    }


def safe_parse(m: str) -> dict | None:
    try:
        return json.loads(m) if m else None
    except json.JSONDecodeError:
        return None


def _format_run_logs(messages: list[str]) -> str:
    """Flatten a run's log messages into the plain text stored on ScraperRun.

    Kept here (rather than in the worker) because it defines the format
    /scraper-runs and /api-logs read back — the worker imports it so both ends
    agree on one representation.
    """
    lines = []
    for m in messages:
        parsed = safe_parse(m)
        if parsed is None:
            lines.append(str(m))
            continue
        value = parsed.get("value", "")
        level = parsed.get("type", "info")
        lines.append(f"[{level.upper()}] {value}" if level not in ("info", "log") else str(value))
    return "\n".join(lines)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/run-scraper/{name}")
def start_scraper(
    name: str,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)

    if not user.bader_credentials:
        raise HTTPException(status_code=400, detail="Bader credentials not saved. Please go to Settings first.")

    if name not in NAMED_SCRAPERS:
        raise HTTPException(status_code=404, detail="Scraper not found")

    # The RAM guard now lives on the worker, at the point of claiming: this
    # process has no idea how much memory the home box has.
    job = enqueue_job(db, name, idinfo.get("email"), payload={"user_id": user.id})
    return {"status": "started", "job_id": job.id}


@router.post("/stop-scraper/{name}")
def stop_scraper(
    name: str,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    if name not in NAMED_SCRAPERS:
        raise HTTPException(status_code=404, detail="Unknown scraper")
    job = active_job(db, name)
    if not job:
        raise HTTPException(status_code=400, detail="Scraper is not running")
    # The worker polls stop_requested, so stopping takes a second or two rather
    # than being instant — it used to set an in-process Event directly.
    return {"status": _request_stop(db, job), "job_id": job.id}


@router.post("/stop-upload/{job_id}")
def stop_upload_job(
    job_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
    if not job or job.scraper_id != UPLOAD_SCRAPER_ID:
        raise HTTPException(status_code=404, detail="Upload job not found")
    if job.status not in ACTIVE_JOB_STATUSES:
        raise HTTPException(status_code=400, detail="Upload job is not running")
    return {"status": _request_stop(db, job)}


@router.get("/scraper-logs/{job_id}")
def scraper_logs(
    job_id: int,
    after: int = 0,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Everything that has happened on a job since log line `after`.

    Replaces the SSE stream: Lambda can't reliably stream a Python response,
    and the in-process buffer the old stream read from doesn't exist any more.
    The client polls this with its last `seq` and gets the tail.
    """
    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    rows = (
        db.query(ScraperJobLog)
        .filter(ScraperJobLog.job_id == job_id, ScraperJobLog.seq > after)
        .order_by(ScraperJobLog.seq)
        .limit(2000)
        .all()
    )

    return {
        **_job_json(job),
        "logs": [
            {"seq": r.seq, "ts": r.ts.isoformat(), "type": r.type, "value": r.value}
            for r in rows
        ],
        "last_seq": rows[-1].seq if rows else after,
    }


@router.get("/upload-jobs")
def get_upload_jobs(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cutoff = datetime.now() - timedelta(hours=12)
    jobs = (
        db.query(ScraperJob)
        .filter(
            ScraperJob.scraper_id == UPLOAD_SCRAPER_ID,
            or_(ScraperJob.status.in_(ACTIVE_JOB_STATUSES), ScraperJob.requested_at >= cutoff),
        )
        .order_by(ScraperJob.requested_at.desc())
        .all()
    )
    return [_job_json(j) for j in jobs]


@router.get("/scrapers-running")
def scrapers_running(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    live = {
        j.scraper_id: j
        for j in db.query(ScraperJob)
        .filter(ScraperJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(ScraperJob.requested_at)
        .all()
        if j.scraper_id != UPLOAD_SCRAPER_ID
    }
    uploads = (
        db.query(ScraperJob)
        .filter(
            ScraperJob.scraper_id == UPLOAD_SCRAPER_ID,
            ScraperJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .all()
    )
    return {
        **{
            name: {
                "running":    name in live,
                "started_by": live[name].requested_by if name in live else None,
                "job_id":     live[name].id if name in live else None,
                "status":     live[name].status if name in live else None,
            }
            for name in NAMED_SCRAPERS
        },
        "upload_jobs": [_job_json(j) for j in uploads],
    }


@router.get("/scraper-last-runs")
def scraper_last_runs(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    result = {}
    for name in NAMED_SCRAPERS:
        run = (
            db.query(ScraperRun)
            .filter(ScraperRun.scraper_id == name)
            .order_by(ScraperRun.ran_at.desc())
            .first()
        )
        result[name] = {
            "id":      run.id if run else None,
            "ran_at":  run.ran_at.isoformat() if run else None,
            "success": run.success if run else None,
            "ran_by":  run.ran_by if run else None,
        }
    return result


@router.get("/scraper-runs")
def scraper_runs(
    limit: int = 30,
    scraper_id: str | None = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    q = db.query(ScraperRun).order_by(ScraperRun.ran_at.desc())
    if scraper_id:
        q = q.filter(ScraperRun.scraper_id == scraper_id)
    runs = q.limit(min(limit, 100)).all()
    return [
        {
            "id":         r.id,
            "scraper_id": r.scraper_id,
            "ran_at":     r.ran_at.isoformat(),
            "success":    r.success,
            "ran_by":     r.ran_by,
        }
        for r in runs
    ]


@router.get("/scraper-runs/{run_id}")
def scraper_run_detail(
    run_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    run = db.query(ScraperRun).filter(ScraperRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return {
        "id":         run.id,
        "scraper_id": run.scraper_id,
        "ran_at":     run.ran_at.isoformat(),
        "success":    run.success,
        "ran_by":     run.ran_by,
        "logs":       run.logs or "",
    }


@router.get("/api-logs")
def api_logs(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_owner),
):
    cutoff = datetime.now() - timedelta(days=RUN_LOG_RETENTION_DAYS)
    runs = (
        db.query(ScraperRun)
        .filter(ScraperRun.ran_at >= cutoff)
        .order_by(ScraperRun.ran_at.desc())
        .all()
    )
    return {
        "retention_days": RUN_LOG_RETENTION_DAYS,
        "runs": [
            {
                "id":         r.id,
                "scraper_id": r.scraper_id,
                "ran_at":     r.ran_at.isoformat(),
                "success":    r.success,
                "ran_by":     r.ran_by,
                "logs":       r.logs or "",
            }
            for r in runs
        ],
    }


def cleanup_old_run_logs():
    """Daily purge (EventBridge on Lambda) of old run records and finished jobs.

    Job rows carry their whole log line-by-line, so they're the bulkier half
    now — the ScraperRun summary is what /api-logs actually reads.
    """
    cutoff = datetime.now() - timedelta(days=RUN_LOG_RETENTION_DAYS)
    db = SessionLocal()
    try:
        deleted = (
            db.query(ScraperRun)
            .filter(ScraperRun.ran_at < cutoff)
            .delete(synchronize_session=False)
        )
        stale_jobs = (
            db.query(ScraperJob.id)
            .filter(
                ScraperJob.requested_at < cutoff,
                ScraperJob.status.notin_(ACTIVE_JOB_STATUSES),
            )
            .all()
        )
        job_ids = [i for (i,) in stale_jobs]
        if job_ids:
            # Explicit, because SQLite (local/tests) doesn't enforce the
            # ondelete=CASCADE the Postgres FK relies on.
            db.query(ScraperJobLog).filter(ScraperJobLog.job_id.in_(job_ids)).delete(
                synchronize_session=False
            )
            db.query(ScraperJob).filter(ScraperJob.id.in_(job_ids)).delete(
                synchronize_session=False
            )
        db.commit()
        if deleted or job_ids:
            print(
                f"[cleanup_old_run_logs] purged {deleted} run record(s) and "
                f"{len(job_ids)} job(s) older than {RUN_LOG_RETENTION_DAYS} days",
                flush=True,
            )
    except Exception as e:
        db.rollback()
        print(f"[cleanup_old_run_logs] failed: {e}", flush=True)
    finally:
        db.close()


# ── Schedules ──────────────────────────────────────────────────────────────────
# Schedule *evaluation* lives on the worker (core/worker), so a cloud outage
# can't stop a scheduled scrape. These endpoints only read and write the rows;
# the worker re-reads them on its own poll and re-registers its cron jobs.

VALID_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _schedule_json(name: str, sched: ScraperSchedule | None) -> dict:
    return {
        "enabled":    sched.enabled if sched else False,
        "days":       sched.days_of_week.split(",") if sched and sched.days_of_week else [],
        "hour":       sched.hour if sched else 22,
        "minute":     sched.minute if sched else 0,
        "runs_as":    sched.user.email if sched and sched.user else None,
        "updated_by": sched.updated_by if sched else None,
        "updated_at": sched.updated_at.isoformat() if sched and sched.updated_at else None,
    }


@router.get("/scraper-schedules")
def get_scraper_schedules(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    schedules = {s.scraper_id: s for s in db.query(ScraperSchedule).all()}
    return {name: _schedule_json(name, schedules.get(name)) for name in NAMED_SCRAPERS}


class SchedulePut(BaseModel):
    enabled: bool
    days: list[str]
    hour: int
    minute: int


@router.put("/scraper-schedules/{name}")
def put_scraper_schedule(
    name: str,
    body: SchedulePut,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    if name not in NAMED_SCRAPERS:
        raise HTTPException(status_code=404, detail="Unknown scraper")

    days = [d for d in body.days if d in VALID_DAYS]
    if body.enabled and not days:
        raise HTTPException(status_code=400, detail="Pick at least one day of the week")
    if not (0 <= body.hour <= 23 and 0 <= body.minute <= 59):
        raise HTTPException(status_code=400, detail="Invalid time")

    user = get_or_create_user(db, idinfo)
    if body.enabled and not user.bader_credentials:
        raise HTTPException(
            status_code=400,
            detail="Scheduled runs use your Bader credentials — save them in Settings first.",
        )

    sched = db.query(ScraperSchedule).filter(ScraperSchedule.scraper_id == name).first()
    if not sched:
        sched = ScraperSchedule(scraper_id=name)
        db.add(sched)

    sched.enabled = body.enabled
    sched.days_of_week = ",".join(d for d in VALID_DAYS if d in days)
    sched.hour = body.hour
    sched.minute = body.minute
    sched.user_id = user.id
    sched.updated_by = user.email
    sched.updated_at = datetime.now()
    db.commit()
    db.refresh(sched)

    return _schedule_json(name, sched)


# ─── Attachment-check qualifications ──────────────────────────────────────────
# The cadet-quali scraper checks each of these (exact Bader qual names) for a
# proof attachment and flags cadets missing one.

@router.get("/attachment-check-quals")
def get_attachment_check_quals(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return {"quals": [q.qual_name for q in db.query(AttachmentCheckQual).order_by(AttachmentCheckQual.qual_name).all()]}


class AttachmentCheckQualsPut(BaseModel):
    quals: list[str]


@router.put("/attachment-check-quals")
def put_attachment_check_quals(
    body: AttachmentCheckQualsPut,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    # Whole-list replace — dedupe (case-insensitive) trimmed non-empty names.
    seen = {}
    for name in body.quals:
        name = name.strip()
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name

    db.query(AttachmentCheckQual).delete()
    for name in seen.values():
        db.add(AttachmentCheckQual(qual_name=name))
    db.commit()

    return {"quals": sorted(seen.values())}
