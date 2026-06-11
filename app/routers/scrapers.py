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

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database.database import engine
from database.models import ScraperRun, StatsSnapshot

from scripts.scraper_calls import (
    info_and_quali_scraper, cadet_event_scraper, event_317_scraper, medical_scraper,
)

from core.db import get_db, get_or_create_user
from core.security import require_staff
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
    state["driver"] = None
    success = False

    def on_driver_ready(driver):
        state["driver"] = driver

    def monitor_timeout():
        time.sleep(SCRAPER_TIMEOUT_SECONDS)
        if state["running"]:
            stop_event.set()
            _quit_driver(state)

    threading.Thread(target=monitor_timeout, daemon=True).start()

    try:
        scraper_func(state["messages"], state["lock"], user_id, db, stop_event, on_driver_ready=on_driver_ready)

        with state["lock"]:
            if stop_event.is_set():
                state["messages"].append(json.dumps({"type": "error", "value": "Scraper timed out."}))
            else:
                if name == "cadet-quali":
                    _save_stats_snapshot(db)
                state["messages"].append(json.dumps({"type": "status", "value": "done"}))
                success = True
    except Exception as e:
        if not stop_event.is_set():
            with state["lock"]:
                state["messages"].append(json.dumps({"type": "error", "value": f"Crash: {str(e)}"}))
    finally:
        state["driver"] = None
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
