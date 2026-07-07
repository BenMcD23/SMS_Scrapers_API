"""Bader scrapers — background runs, live SSE log streams, start/stop.

Named scrapers (cadet-quali, cadet-event, 317-event, medical) each have their
own state slot and can run in parallel.  Upload jobs each get a unique UUID
job_id so any number can run simultaneously (subject to the RAM guard).
"""

import asyncio
import json
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import SessionLocal, engine
from database.models import ScraperRun, ScraperSchedule, StatsSnapshot, AttachmentCheckQual

from scripts.scraper_calls import (
    info_and_quali_scraper, cadet_event_scraper, event_317_scraper, medical_scraper,
    upload_qualifications_scraper,
)
from scripts.staff_scraper import staff_scraper

from core.db import get_db, get_or_create_user
from core.scheduler import scheduler
from core.security import require_staff, require_owner
from routers.cadets import invalidate_cadet_caches
from routers.stats import compute_stats

router = APIRouter()

SCRAPER_TIMEOUT_SECONDS = 900
RUN_LOG_RETENTION_DAYS = 7

# ── Per-scraper state for the 4 named scrapers ──────────────────────────────────────────────

NAMED_SCRAPERS = ["cadet-quali", "cadet-event", "317-event", "medical", "staff"]

SCRAPER_FUNCS = {
    "cadet-quali": info_and_quali_scraper,
    "cadet-event": cadet_event_scraper,
    "317-event":   event_317_scraper,
    "medical":     medical_scraper,
    "staff":       staff_scraper,
}

named_scraper_states: dict = {
    name: {
        "messages":    [],
        "lock":        threading.Lock(),
        "running":     False,
        "started_by":  None,
        "stop_event":  threading.Event(),
        "stop_reason": None,
        "run_id":      0,
        "context":     None,
    }
    for name in NAMED_SCRAPERS
}

# ── Per-job upload state ────────────────────────────────────────────────────────────────

upload_jobs: dict[str, dict] = {}
upload_jobs_lock = threading.Lock()


def create_upload_job(started_by: str) -> tuple[str, dict]:
    from scripts.scraper_utils import check_ram_ok
    ram_ok, available_mb = check_ram_ok()
    if not ram_ok:
        raise HTTPException(
            status_code=503,
            detail=f"Server RAM too low ({available_mb:.0f} MB available, need 500 MB). Try again shortly.",
        )
    job_id = uuid.uuid4().hex[:8]
    state = {
        "job_id":     job_id,
        "messages":   [],
        "lock":       threading.Lock(),
        "running":    True,
        "started_by": started_by,
        "stop_event": threading.Event(),
        "context":    None,
        "started_at": datetime.now(),
        "finished_at": None,
    }
    with upload_jobs_lock:
        upload_jobs[job_id] = state
    return job_id, state


def run_upload_job(job_id: str, user_id: int, user_email: str, assessment_ids: list[int]):
    state = upload_jobs[job_id]
    db = Session(engine)
    stop_event = state["stop_event"]
    success = False

    def on_context_ready(ctx):
        state["context"] = ctx

    def monitor_timeout():
        time.sleep(SCRAPER_TIMEOUT_SECONDS)
        if state["running"]:
            stop_event.set()
            ctx = state.get("context")
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass

    threading.Thread(target=monitor_timeout, daemon=True).start()

    try:
        upload_qualifications_scraper(
            state["messages"],
            state["lock"],
            user_id,
            db,
            stop_event,
            assessment_ids=assessment_ids,
            on_context_ready=on_context_ready,
        )

        with state["lock"]:
            if stop_event.is_set():
                state["messages"].append(json.dumps({"type": "error", "value": "Scraper timed out."}))
            else:
                state["messages"].append(json.dumps({"type": "status", "value": "done"}))
                success = True
    except Exception as e:
        print(f"[upload-job {job_id}] CRASH:\n" + traceback.format_exc(), flush=True)
        with state["lock"]:
            state["messages"].append(json.dumps({"type": "error", "value": f"Crash: {type(e).__name__}: {str(e)}"}))
    finally:
        try:
            run_db = Session(engine)
            run_db.add(ScraperRun(
                scraper_id="upload-qualifications",
                ran_at=datetime.now(),
                success=success,
                ran_by=user_email,
                logs=_format_run_logs(state["messages"]),
            ))
            run_db.commit()
            run_db.close()
        except Exception as rec_err:
            print(f"[scraper run record] failed: {rec_err}", flush=True)
        db.close()
        state["running"] = False
        state["context"] = None
        state["finished_at"] = datetime.now()


def _quit_context(state: dict):
    ctx = state.get("context")
    if ctx:
        try:
            ctx.close()
        except Exception:
            pass
        state["context"] = None


def _save_stats_snapshot(db: Session):
    try:
        snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
        db.add(snapshot)
        db.commit()
    except Exception as snap_err:
        print(f"[stats snapshot] failed: {snap_err}")


def run_named_scraper_task(name: str, scraper_func, user_id: int, user_email: str):
    state = named_scraper_states[name]
    db = Session(engine)
    stop_event = state["stop_event"]
    stop_event.clear()
    state["stop_reason"] = None
    state["run_id"] += 1
    run_id = state["run_id"]
    state["context"] = None
    success = False

    def on_context_ready(ctx):
        state["context"] = ctx

    def monitor_timeout():
        time.sleep(SCRAPER_TIMEOUT_SECONDS)
        if state["running"] and state["run_id"] == run_id:
            state["stop_reason"] = state["stop_reason"] or "timeout"
            stop_event.set()
            _quit_context(state)

    threading.Thread(target=monitor_timeout, daemon=True).start()

    def append_stop_outcome():
        if state["stop_reason"] == "manual":
            state["messages"].append(json.dumps({"type": "status", "value": "stopped"}))
        else:
            state["messages"].append(json.dumps({"type": "error", "value": "Scraper timed out."}))

    try:
        scraper_func(state["messages"], state["lock"], user_id, db, stop_event, on_context_ready=on_context_ready)

        with state["lock"]:
            if stop_event.is_set():
                append_stop_outcome()
            else:
                if name == "cadet-quali":
                    _save_stats_snapshot(db)
                invalidate_cadet_caches()
                state["messages"].append(json.dumps({"type": "status", "value": "done"}))
                success = True
    except Exception as e:
        with state["lock"]:
            if stop_event.is_set():
                append_stop_outcome()
            else:
                state["messages"].append(json.dumps({"type": "error", "value": f"Crash: {str(e)}"}))
    finally:
        state["context"] = None
        if state["stop_reason"] != "manual":
            try:
                run_db = Session(engine)
                run_db.add(ScraperRun(
                    scraper_id=name,
                    ran_at=datetime.now(),
                    success=success,
                    ran_by=user_email,
                    logs=_format_run_logs(state["messages"]),
                ))
                run_db.commit()
                run_db.close()
            except Exception as rec_err:
                print(f"[scraper run record] failed: {rec_err}")
        db.close()
        state["running"] = False
        state["started_by"] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────────────────

@router.get("/run-scraper/{name}")
async def start_scraper(
    name: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)

    if not user.bader_credentials:
        raise HTTPException(status_code=400, detail="Bader credentials not saved. Please go to Settings first.")

    if name not in SCRAPER_FUNCS:
        raise HTTPException(status_code=404, detail="Scraper not found")

    # RAM guard for named scrapers too
    from scripts.scraper_utils import check_ram_ok
    ram_ok, available_mb = check_ram_ok()
    if not ram_ok:
        raise HTTPException(
            status_code=503,
            detail=f"Server RAM too low ({available_mb:.0f} MB available, need 500 MB). Try again shortly.",
        )

    state = named_scraper_states[name]
    with state["lock"]:
        if state["running"]:
            raise HTTPException(status_code=400, detail="Scraper already running")
        state["running"] = True
        state["started_by"] = idinfo.get("email")
        state["messages"].clear()
        state["messages"].append(json.dumps({
            "type": "status", "value": "running",
            "started_by": state["started_by"], "scraper_name": name,
        }))

    background_tasks.add_task(
        run_named_scraper_task, name, SCRAPER_FUNCS[name], user.id, idinfo.get("email", "")
    )
    return {"status": "started"}


@router.post("/stop-scraper/{name}")
async def stop_scraper(name: str, idinfo: dict = Depends(require_staff)):
    if name not in named_scraper_states:
        raise HTTPException(status_code=404, detail="Unknown scraper")
    state = named_scraper_states[name]
    if not state["running"]:
        raise HTTPException(status_code=400, detail="Scraper is not running")
    state["stop_reason"] = "manual"
    state["stop_event"].set()
    _quit_context(state)
    with state["lock"]:
        state["messages"].append(json.dumps({"type": "warning", "value": "[STOPPED] Scraper was manually stopped."}))
    return {"status": "stopping"}


def safe_parse(m: str) -> dict | None:
    try:
        return json.loads(m) if m else None
    except json.JSONDecodeError:
        return None


def _format_run_logs(messages: list[str]) -> str:
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


def _staff_from_header_or_query(authorization: str, token: str):
    auth = authorization or (f"Bearer {token}" if token else None)
    return require_staff(auth)


@router.get("/scraper-stream/{name}")
async def named_scraper_stream(
    name: str,
    authorization: str = Header(None),
    token: str = Query(None),
):
    _staff_from_header_or_query(authorization, token)

    if name not in named_scraper_states:
        raise HTTPException(status_code=404, detail="Unknown scraper")

    state = named_scraper_states[name]

    async def event_generator():
        with state["lock"]:
            if state["running"] and state["started_by"]:
                yield f"data: {json.dumps({'type': 'status', 'value': 'running', 'started_by': state['started_by'], 'scraper_name': name})}\n\n"
            last_idx = len(state["messages"])
            for msg in state["messages"]:
                yield f"data: {msg}\n\n"

        while True:
            await asyncio.sleep(0.5)
            with state["lock"]:
                current_len = len(state["messages"])
            if last_idx < current_len:
                with state["lock"]:
                    new_msgs = state["messages"][last_idx:]
                for msg in new_msgs:
                    yield f"data: {msg}\n\n"
                last_idx = current_len

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/upload-stream/{job_id}")
async def upload_stream(
    job_id: str,
    authorization: str = Header(None),
    token: str = Query(None),
):
    _staff_from_header_or_query(authorization, token)

    with upload_jobs_lock:
        state = upload_jobs.get(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Upload job not found")

    async def event_generator():
        with state["lock"]:
            last_idx = len(state["messages"])
            for msg in state["messages"]:
                yield f"data: {msg}\n\n"

        while True:
            await asyncio.sleep(0.5)
            with state["lock"]:
                current_len = len(state["messages"])
            if last_idx < current_len:
                with state["lock"]:
                    new_msgs = state["messages"][last_idx:]
                for msg in new_msgs:
                    yield f"data: {msg}\n\n"
                last_idx = current_len

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/upload-jobs")
async def get_upload_jobs(idinfo: dict = Depends(require_staff)):
    with upload_jobs_lock:
        jobs = list(upload_jobs.values())
    return [
        {
            "job_id":      j["job_id"],
            "running":     j["running"],
            "started_by":  j["started_by"],
            "started_at":  j["started_at"].isoformat() if j["started_at"] else None,
            "finished_at": j["finished_at"].isoformat() if j["finished_at"] else None,
        }
        for j in jobs
    ]


@router.post("/stop-upload/{job_id}")
async def stop_upload_job(job_id: str, idinfo: dict = Depends(require_staff)):
    with upload_jobs_lock:
        state = upload_jobs.get(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Upload job not found")
    if not state["running"]:
        raise HTTPException(status_code=400, detail="Upload job is not running")
    state["stop_event"].set()
    _quit_context(state)
    return {"status": "stopping"}


@router.get("/scrapers-running")
async def scrapers_running(idinfo: dict = Depends(require_staff)):
    with upload_jobs_lock:
        jobs_snapshot = list(upload_jobs.values())
    return {
        **{
            name: {"running": state["running"], "started_by": state["started_by"]}
            for name, state in named_scraper_states.items()
        },
        "upload_jobs": [
            {
                "job_id":     j["job_id"],
                "running":    j["running"],
                "started_by": j["started_by"],
            }
            for j in jobs_snapshot
        ],
    }


@router.get("/scraper-last-runs")
async def scraper_last_runs(
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
async def scraper_runs(
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
async def scraper_run_detail(
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
async def api_logs(
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
    cutoff = datetime.now() - timedelta(days=RUN_LOG_RETENTION_DAYS)
    db = Session(engine)
    try:
        deleted = (
            db.query(ScraperRun)
            .filter(ScraperRun.ran_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            print(f"[cleanup_old_run_logs] purged {deleted} run record(s) older than {RUN_LOG_RETENTION_DAYS} days", flush=True)
    except Exception as e:
        db.rollback()
        print(f"[cleanup_old_run_logs] failed: {e}", flush=True)
    finally:
        db.close()


# ── Schedules ──────────────────────────────────────────────────────────────────

VALID_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _run_scheduled_scraper(name: str):
    db = SessionLocal()
    claimed = False
    try:
        sched = db.query(ScraperSchedule).filter(ScraperSchedule.scraper_id == name).first()
        if not sched or not sched.enabled or not sched.user:
            return
        if not sched.user.bader_credentials:
            print(f"[scheduled scraper] {name}: {sched.user.email} has no Bader credentials saved, skipping")
            return

        user_id, email = sched.user.id, sched.user.email
        state = named_scraper_states[name]
        with state["lock"]:
            if state["running"]:
                print(f"[scheduled scraper] {name}: already running, skipping")
                return
            state["running"] = True
            state["started_by"] = f"schedule ({email})"
            state["messages"].clear()
            state["messages"].append(json.dumps({
                "type": "status", "value": "running",
                "started_by": state["started_by"], "scraper_name": name,
            }))
            claimed = True
    finally:
        db.close()

    if claimed:
        run_named_scraper_task(name, SCRAPER_FUNCS[name], user_id, email)


def register_schedule_jobs():
    db = SessionLocal()
    try:
        schedules = {s.scraper_id: s for s in db.query(ScraperSchedule).all()}
        for name in NAMED_SCRAPERS:
            job_id = f"scraper-sched-{name}"
            sched = schedules.get(name)
            if sched and sched.enabled and sched.days_of_week:
                scheduler.add_job(
                    _run_scheduled_scraper,
                    CronTrigger(
                        day_of_week=sched.days_of_week,
                        hour=sched.hour,
                        minute=sched.minute,
                        timezone="Europe/London",
                    ),
                    args=[name],
                    id=job_id,
                    replace_existing=True,
                )
            else:
                try:
                    scheduler.remove_job(job_id)
                except Exception:
                    pass
    finally:
        db.close()


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
async def get_scraper_schedules(
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
async def put_scraper_schedule(
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

    register_schedule_jobs()
    return _schedule_json(name, sched)


# ─── Attachment-check qualifications ──────────────────────────────────────────
# The cadet-quali scraper checks each of these (exact Bader qual names) for a
# proof attachment and flags cadets missing one.

@router.get("/attachment-check-quals")
async def get_attachment_check_quals(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return {"quals": [q.qual_name for q in db.query(AttachmentCheckQual).order_by(AttachmentCheckQual.qual_name).all()]}


class AttachmentCheckQualsPut(BaseModel):
    quals: list[str]


@router.put("/attachment-check-quals")
async def put_attachment_check_quals(
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
