"""Bader scrapers — background runs, live SSE log streams, start/stop.

There are two kinds of scraper state:
  - the four named scrapers (cadet-quali, cadet-event, 317-event, medical),
    each with its own state slot so they can run in parallel
  - one legacy "global" slot used by the upload-to-bader job
"""

import asyncio
import json
import threading
import time
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import SessionLocal, engine
from database.models import ScraperRun, ScraperSchedule, StatsSnapshot

from scripts.scraper_calls import (
    info_and_quali_scraper, cadet_event_scraper, event_317_scraper, medical_scraper,
)

from core.db import get_db, get_or_create_user
from core.scheduler import scheduler
from core.security import require_staff
from routers.cadets import invalidate_cadet_caches
from routers.stats import compute_stats

router = APIRouter()

SCRAPER_TIMEOUT_SECONDS = 900

# ── Global scraper state (upload-to-bader) ────────────────────────────────────

scraper_messages = []
scraper_lock = threading.Lock()

scraper_running = False
scraper_state_lock = threading.Lock()

current_scraper_user = None
current_scraper_name = None

# ── Per-scraper state for the 4 named scrapers ────────────────────────────────

NAMED_SCRAPERS = ["cadet-quali", "cadet-event", "317-event", "medical"]

SCRAPER_FUNCS = {
    "cadet-quali": info_and_quali_scraper,
    "cadet-event": cadet_event_scraper,
    "317-event": event_317_scraper,
    "medical": medical_scraper,
}

named_scraper_states: dict = {
    name: {
        "messages": [],
        "lock": threading.Lock(),
        "running": False,
        "started_by": None,
        "stop_event": threading.Event(),
        "stop_reason": None,   # None | "manual" | "timeout" — why stop_event was set
        "run_id": 0,           # incremented per run so stale timeout monitors can't kill a newer run
        "driver": None,   # live Selenium WebDriver reference while scraper runs
    }
    for name in NAMED_SCRAPERS
}


def _quit_driver(state: dict):
    """Forcefully quit the WebDriver stored in a scraper state slot, if any."""
    driver = state.get("driver")
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
        state["driver"] = None


def _save_stats_snapshot(db: Session):
    """Auto-snapshot squadron stats after a successful cadet-quali run."""
    try:
        snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
        db.add(snapshot)
        db.commit()
    except Exception as snap_err:
        print(f"[stats snapshot] failed: {snap_err}")


def run_scraper_task(scraper_func, user_id: int):
    """Background task for the legacy global slot (upload-to-bader)."""
    global scraper_running, current_scraper_user, current_scraper_name
    db = Session(engine)
    stop_event = threading.Event()

    def monitor_timeout():
        time.sleep(SCRAPER_TIMEOUT_SECONDS)
        if scraper_running:
            stop_event.set()

    threading.Thread(target=monitor_timeout, daemon=True).start()

    try:
        scraper_func(scraper_messages, scraper_lock, user_id, db, stop_event)

        with scraper_lock:
            if stop_event.is_set():
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper timed out."}))
            else:
                if current_scraper_name == "cadet-quali":
                    _save_stats_snapshot(db)
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Crash: {str(e)}"}))
    finally:
        db.close()
        with scraper_state_lock:
            scraper_running = False
        current_scraper_user = None
        current_scraper_name = None


def run_named_scraper_task(name: str, scraper_func, user_id: int, user_email: str):
    state = named_scraper_states[name]
    db = Session(engine)
    stop_event = state["stop_event"]
    stop_event.clear()  # reset from any previous run
    state["stop_reason"] = None
    state["run_id"] += 1
    run_id = state["run_id"]
    state["driver"] = None
    success = False

    def on_driver_ready(driver):
        state["driver"] = driver

    def monitor_timeout():
        time.sleep(SCRAPER_TIMEOUT_SECONDS)
        # Only time out the run this monitor was started for
        if state["running"] and state["run_id"] == run_id:
            state["stop_reason"] = state["stop_reason"] or "timeout"
            stop_event.set()
            _quit_driver(state)

    threading.Thread(target=monitor_timeout, daemon=True).start()

    def append_stop_outcome():
        """Terminal stream message when the run was interrupted."""
        if state["stop_reason"] == "manual":
            state["messages"].append(json.dumps({"type": "status", "value": "stopped"}))
        else:
            state["messages"].append(json.dumps({"type": "error", "value": "Scraper timed out."}))

    try:
        scraper_func(state["messages"], state["lock"], user_id, db, stop_event, on_driver_ready=on_driver_ready)

        with state["lock"]:
            if stop_event.is_set():
                append_stop_outcome()
            else:
                if name == "cadet-quali":
                    _save_stats_snapshot(db)
                # Scraper imports change cadet-derived data — drop stale caches
                invalidate_cadet_caches()
                state["messages"].append(json.dumps({"type": "status", "value": "done"}))
                success = True
    except Exception as e:
        with state["lock"]:
            if stop_event.is_set():
                # Killing the browser makes blocking Selenium calls throw — that's
                # the expected stop path, not a crash
                append_stop_outcome()
            else:
                state["messages"].append(json.dumps({"type": "error", "value": f"Crash: {str(e)}"}))
    finally:
        state["driver"] = None
        # Manual stops aren't recorded — "last run" keeps showing the last real run
        if state["stop_reason"] != "manual":
            try:
                run_db = Session(engine)
                run_db.add(ScraperRun(
                    scraper_id=name,
                    ran_at=datetime.now(),
                    success=success,
                    ran_by=user_email,
                ))
                run_db.commit()
                run_db.close()
            except Exception as rec_err:
                print(f"[scraper run record] failed: {rec_err}")
        db.close()
        state["running"] = False
        state["started_by"] = None


def claim_global_scraper(name: str, started_by: str):
    """Reserve the global slot for a new job, or 400 if one is already running."""
    global scraper_running, current_scraper_user, current_scraper_name
    with scraper_state_lock:
        if scraper_running:
            raise HTTPException(status_code=400, detail="A scraper is already running.")
        scraper_running = True

    current_scraper_user = started_by
    current_scraper_name = name

    with scraper_lock:
        scraper_messages.clear()
        scraper_messages.append(
            json.dumps({
                "type": "status",
                "value": "running",
                "started_by": started_by,
                "scraper_name": name,
            })
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/run-scraper/{name}")
async def start_scraper(
    name: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = get_or_create_user(db, idinfo)

    if not user.bader_credentials:
        raise HTTPException(
            status_code=400,
            detail="Bader credentials not saved. Please go to Settings first.",
        )

    if name not in SCRAPER_FUNCS:
        raise HTTPException(status_code=404, detail="Scraper not found")

    state = named_scraper_states[name]
    with state["lock"]:
        if state["running"]:
            raise HTTPException(status_code=400, detail="Scraper already running")
        state["running"] = True
        state["started_by"] = idinfo.get("email")
        state["messages"].clear()
        state["messages"].append(
            json.dumps({
                "type": "status",
                "value": "running",
                "started_by": state["started_by"],
                "scraper_name": name,
            })
        )

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
    # Signal the loop to stop, and kill the browser so blocking Selenium calls throw now
    state["stop_reason"] = "manual"
    state["stop_event"].set()
    _quit_driver(state)
    with state["lock"]:
        state["messages"].append(json.dumps({"type": "warning", "value": "[STOPPED] Scraper was manually stopped."}))
    return {"status": "stopping"}


def safe_parse(m: str) -> dict | None:
    try:
        return json.loads(m) if m else None
    except json.JSONDecodeError:
        return None


def _staff_from_header_or_query(authorization: str, token: str):
    # EventSource can't set headers, so SSE endpoints also accept ?token=
    auth = authorization or (f"Bearer {token}" if token else None)
    return require_staff(auth)


@router.get("/scraper-stream")
async def scraper_stream(
    authorization: str = Header(None),
    token: str = Query(None),
):
    _staff_from_header_or_query(authorization, token)

    async def event_generator():
        # Replay current state to any tab that connects late
        with scraper_lock:
            if scraper_running and current_scraper_user:
                catchup = json.dumps({
                    "type": "status",
                    "value": "running",
                    "started_by": current_scraper_user,
                    "scraper_name": current_scraper_name,
                })
                yield f"data: {catchup}\n\n"

            last_idx = len(scraper_messages)
            for msg in scraper_messages:
                parsed = safe_parse(msg)
                if parsed and parsed.get("type") == "log":
                    yield f"data: {msg}\n\n"

        # Then keep polling for new messages until the client disconnects
        while True:
            await asyncio.sleep(0.5)
            with scraper_lock:
                current_len = len(scraper_messages)
            if last_idx < current_len:
                with scraper_lock:
                    new_messages = scraper_messages[last_idx:]
                for msg in new_messages:
                    yield f"data: {msg}\n\n"
                last_idx = current_len

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/scraper-status")
async def scraper_status(idinfo: dict = Depends(require_staff)):
    with scraper_lock:
        logs = [
            json.loads(m).get("value", m)
            for m in scraper_messages
            if safe_parse(m) and safe_parse(m).get("type") == "log"
        ]
    return {
        "running": scraper_running,
        "started_by": current_scraper_user,
        "scraper_name": current_scraper_name,
        "recent_logs": logs[-50:],
    }


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
            "ran_at": run.ran_at.isoformat() if run else None,
            "success": run.success if run else None,
            "ran_by": run.ran_by if run else None,
        }
    return result


@router.get("/scrapers-running")
async def scrapers_running(idinfo: dict = Depends(require_staff)):
    return {
        name: {
            "running": state["running"],
            "started_by": state["started_by"],
        }
        for name, state in named_scraper_states.items()
    }


# ── Schedules ─────────────────────────────────────────────────────────────────

VALID_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _run_scheduled_scraper(name: str):
    """APScheduler job body — start a scraper run with the schedule saver's credentials."""
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
                "type": "status",
                "value": "running",
                "started_by": state["started_by"],
                "scraper_name": name,
            }))
            claimed = True
    finally:
        db.close()

    if claimed:
        run_named_scraper_task(name, SCRAPER_FUNCS[name], user_id, email)


def register_schedule_jobs():
    """Sync APScheduler jobs with the DB schedules — at startup and after every save."""
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
        "enabled": sched.enabled if sched else False,
        "days": sched.days_of_week.split(",") if sched and sched.days_of_week else [],
        "hour": sched.hour if sched else 22,
        "minute": sched.minute if sched else 0,
        "runs_as": sched.user.email if sched and sched.user else None,
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
    sched.days_of_week = ",".join(d for d in VALID_DAYS if d in days)  # keep weekday order
    sched.hour = body.hour
    sched.minute = body.minute
    sched.user_id = user.id
    sched.updated_by = user.email
    sched.updated_at = datetime.now()
    db.commit()
    db.refresh(sched)

    register_schedule_jobs()
    return _schedule_json(name, sched)
