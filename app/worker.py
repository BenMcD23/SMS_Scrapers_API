"""Scraper worker — runs on the home box, talks only outbound.

This is the other half of the queue in routers/scrapers.py. The API (in the
cloud) writes `Scraper_Jobs` rows; this process claims them, drives Playwright
against Bader, and writes the logs and the resulting `Scraper_Run` back. It
never listens on a port, so nothing has to be able to reach the house — a
Tailscale outage pauses scrapes instead of taking the site down.

Two things make this small:

* `DbLogSink` implements `.append()`, which is the entire interface every
  scraper in scripts/ uses to log (`messages.append(json.dumps({...}))` under
  `with lock:`). Passing a sink instead of a list means none of the scraper
  scripts change.
* Schedule evaluation stays here rather than in the cloud, so a cloud outage
  doesn't stop a scheduled scrape either.

Run it with `python -m worker` (see Dockerfile.worker).
"""

import json
import os
import signal
import socket
import threading
import time
import traceback
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database.database import SessionLocal, engine
from database.models import (
    ScraperJob, ScraperJobLog, ScraperRun, ScraperSchedule, StatsSnapshot,
)

from routers.scrapers import NAMED_SCRAPERS, UPLOAD_SCRAPER_ID, _format_run_logs
from routers.stats import compute_stats

from scripts.scraper_calls import (
    info_and_quali_scraper, cadet_event_scraper, event_317_scraper, medical_scraper,
    upload_qualifications_scraper, absence_scraper,
)
from scripts.staff_scraper import staff_scraper
from scripts.scraper_utils import check_ram_ok

SCRAPER_FUNCS = {
    "cadet-quali": info_and_quali_scraper,
    "cadet-event": cadet_event_scraper,
    "317-event":   event_317_scraper,
    "medical":     medical_scraper,
    "staff":       staff_scraper,
    "absences":    absence_scraper,
}

# A run is killed when it stops making *progress*, not on total runtime: a full
# cadet-quali sweep of a large squadron legitimately runs past any fixed
# wall-clock cap (it was being killed mid-save after the scrape had finished and
# reported as a timeout), while a wedged Bader page goes quiet straight away.
SCRAPER_IDLE_TIMEOUT_SECONDS = 300
# Backstop for a run that keeps logging but never finishes.
SCRAPER_MAX_RUNTIME_SECONDS = 3 * 60 * 60
WATCHDOG_POLL_SECONDS = 15

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
# How often the claim loop asks for work. Also the ceiling on how long a stop
# takes to be noticed, since the same interval polls stop_requested.
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "2"))
# Named scrapers used to run in parallel on the API's threadpool; the RAM guard
# was the only real limit. Keep both: a hard cap and the guard.
MAX_CONCURRENT_JOBS = int(os.getenv("WORKER_MAX_JOBS", "4"))
# A claimed/running job nobody has touched for this long belongs to a worker
# that died. Comfortably past the idle watchdog, so a live-but-slow run is
# never mistaken for a dead one.
STALE_JOB_SECONDS = int(os.getenv("WORKER_STALE_JOB_SECONDS", "900"))
# A job that reliably kills the worker must not be requeued forever.
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
SCHEDULE_REFRESH_SECONDS = 60

_shutdown = threading.Event()


def log(msg: str) -> None:
    print(f"[worker] {msg}", flush=True)


# ── Log sink ──────────────────────────────────────────────────────────────────

class DbLogSink:
    """A list-shaped object that writes what it's given to Scraper_Job_Logs.

    Every scraper logs by appending a JSON string to a shared list under a
    lock, so implementing `append` (and `__len__`, which the watchdog reads as
    progress) is all that's needed to redirect them at the database — none of
    the scripts in scripts/ change.

    Writes are batched by a background flusher: a cadet-quali run emits
    thousands of lines, and one INSERT each across the network to Neon would
    dominate the run time.
    """

    FLUSH_SECONDS = 1.0
    MAX_BATCH = 500

    def __init__(self, job_id: int):
        self.job_id = job_id
        # The raw payloads, kept so the run's ScraperRun record can be built by
        # the same _format_run_logs the old in-memory buffer used.
        self.messages: list[str] = []
        self._pending: list[tuple[int, datetime, str]] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    # -- list interface the scrapers use ---------------------------------

    def append(self, msg) -> None:
        payload = msg if isinstance(msg, str) else str(msg)
        with self._lock:
            self._seq += 1
            self.messages.append(payload)
            self._pending.append((self._seq, datetime.now(), payload))

    def __len__(self) -> int:
        return len(self.messages)

    def __iter__(self):
        return iter(list(self.messages))

    # -- persistence -----------------------------------------------------

    def _take(self) -> list[tuple[int, datetime, str]]:
        with self._lock:
            batch, self._pending = self._pending[: self.MAX_BATCH], self._pending[self.MAX_BATCH :]
        return batch

    @staticmethod
    def _row(job_id: int, seq: int, ts: datetime, payload: str) -> ScraperJobLog:
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if not isinstance(parsed, dict):
            # Some helpers (push_to_google_apps_script) append bare strings.
            return ScraperJobLog(job_id=job_id, seq=seq, ts=ts, type="info", value=payload)
        return ScraperJobLog(
            job_id=job_id,
            seq=seq,
            ts=ts,
            type=str(parsed.get("type", "info")),
            value=str(parsed.get("value", "")),
        )

    def flush(self) -> None:
        while True:
            batch = self._take()
            if not batch:
                return
            db = SessionLocal()
            try:
                db.add_all([self._row(self.job_id, seq, ts, payload) for seq, ts, payload in batch])
                db.commit()
            except SQLAlchemyError as e:
                db.rollback()
                # Losing a log line must never kill the scrape it belongs to.
                log(f"job {self.job_id}: could not write {len(batch)} log line(s): {e}")
            finally:
                db.close()

    def _flush_loop(self) -> None:
        while not self._done.is_set():
            self._done.wait(self.FLUSH_SECONDS)
            self.flush()

    def close(self) -> None:
        self._done.set()
        self.flush()


# ── Watchdog & stop polling ───────────────────────────────────────────────────

def start_watchdog(sink: DbLogSink, running, label: str, on_timeout) -> None:
    """Watch a running scraper and trip `on_timeout` when it stalls.

    Progress is measured by the run appending to its own log, so a scrape that
    is slow but alive keeps itself alive. Unchanged from the API version except
    that the log it watches is now rows rather than a list in memory.
    """
    def watch():
        started = last_progress = time.monotonic()
        seen = len(sink)
        while True:
            time.sleep(WATCHDOG_POLL_SECONDS)
            if not running():
                return
            now = time.monotonic()
            if len(sink) != seen:
                seen = len(sink)
                last_progress = now
            idle = now - last_progress
            if idle >= SCRAPER_IDLE_TIMEOUT_SECONDS or now - started >= SCRAPER_MAX_RUNTIME_SECONDS:
                # Killing a run is otherwise invisible outside the job log, which
                # makes "Scraper timed out." impossible to chase in the API logs.
                log(
                    f"{label} killed after {now - started:.0f}s "
                    f"({idle:.0f}s since its last log line)"
                )
                on_timeout()
                return

    threading.Thread(target=watch, daemon=True).start()


def start_stop_poller(job_id: int, running, on_stop) -> None:
    """Notice a stop requested by the cloud API.

    The API can't reach into this process, so a stop is a flag on the row and
    takes up to POLL_SECONDS to land rather than being instant.
    """
    def poll():
        while running():
            time.sleep(POLL_SECONDS)
            db = SessionLocal()
            try:
                stop = (
                    db.query(ScraperJob.stop_requested)
                    .filter(ScraperJob.id == job_id)
                    .scalar()
                )
            except SQLAlchemyError as e:
                log(f"job {job_id}: stop poll failed: {e}")
                stop = False
            finally:
                db.close()
            if stop:
                on_stop()
                return

    threading.Thread(target=poll, daemon=True).start()


# ── Claiming ──────────────────────────────────────────────────────────────────

_CLAIM_PG = text(
    """
    UPDATE "Scraper_Jobs"
       SET status = 'claimed', claimed_at = :now, worker_id = :worker,
           attempts = attempts + 1
     WHERE id = (
             SELECT id FROM "Scraper_Jobs"
              WHERE status = 'queued'
              ORDER BY requested_at
              LIMIT 1
              FOR UPDATE SKIP LOCKED
           )
    RETURNING id
    """
)

# SQLite (local dev) has no row locking, but it also has exactly one writer.
_CLAIM_SQLITE = text(
    """
    UPDATE "Scraper_Jobs"
       SET status = 'claimed', claimed_at = :now, worker_id = :worker,
           attempts = attempts + 1
     WHERE id = (
             SELECT id FROM "Scraper_Jobs"
              WHERE status = 'queued'
              ORDER BY requested_at
              LIMIT 1
           )
    RETURNING id
    """
)


def claim_job() -> ScraperJob | None:
    """Take the oldest queued job, or None. Concurrency-safe: the UPDATE is the
    claim, so two workers (or a worker and a retry) can't take the same row."""
    stmt = _CLAIM_PG if engine.dialect.name == "postgresql" else _CLAIM_SQLITE
    db = SessionLocal()
    try:
        row = db.execute(stmt, {"now": datetime.now(), "worker": WORKER_ID}).first()
        db.commit()
        if not row:
            return None
        return db.query(ScraperJob).filter(ScraperJob.id == row[0]).first()
    except SQLAlchemyError as e:
        db.rollback()
        log(f"claim failed: {e}")
        return None
    finally:
        db.close()


def _finish(job_id: int, status: str, note: str | None = None) -> None:
    db = SessionLocal()
    try:
        job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        if job:
            job.status = status
            job.finished_at = datetime.now()
            db.commit()
        if note:
            log(f"job {job_id}: {note}")
    except SQLAlchemyError as e:
        db.rollback()
        log(f"job {job_id}: could not mark {status}: {e}")
    finally:
        db.close()


def _set_running(job_id: int) -> None:
    db = SessionLocal()
    try:
        db.query(ScraperJob).filter(ScraperJob.id == job_id).update({"status": "running"})
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        log(f"job {job_id}: could not mark running: {e}")
    finally:
        db.close()


def _record_run(scraper_id: str, success: bool, ran_by: str | None, messages: list[str]) -> None:
    """Write the ScraperRun summary /scraper-runs and /api-logs read back."""
    db = SessionLocal()
    try:
        db.add(ScraperRun(
            scraper_id=scraper_id,
            ran_at=datetime.now(),
            success=success,
            ran_by=ran_by,
            logs=_format_run_logs(messages),
        ))
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        log(f"[scraper run record] failed: {e}")
    finally:
        db.close()


def _save_stats_snapshot(db: Session) -> None:
    try:
        snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
        db.add(snapshot)
        db.commit()
    except Exception as snap_err:
        log(f"[stats snapshot] failed: {snap_err}")


# ── Running a job ─────────────────────────────────────────────────────────────

def run_job(job: ScraperJob) -> None:
    """Execute one claimed job to completion and record the outcome."""
    job_id = job.id
    name = job.scraper_id
    payload = job.payload or {}
    user_id = payload.get("user_id")
    ran_by = job.requested_by

    sink = DbLogSink(job_id)
    lock = threading.Lock()
    stop_event = threading.Event()
    state = {"context": None, "running": True, "stop_reason": None}

    sink.append(json.dumps({
        "type": "status", "value": "running",
        "started_by": ran_by, "scraper_name": name,
    }))
    _set_running(job_id)

    def on_context_ready(ctx):
        state["context"] = ctx

    def quit_context():
        ctx = state.get("context")
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
            state["context"] = None

    def on_timeout():
        state["stop_reason"] = state["stop_reason"] or "timeout"
        stop_event.set()
        quit_context()

    def on_manual_stop():
        state["stop_reason"] = "manual"
        stop_event.set()
        with lock:
            sink.append(json.dumps({"type": "warning", "value": "[STOPPED] Scraper was manually stopped."}))
        quit_context()

    start_watchdog(sink, lambda: state["running"], f"{name} (job {job_id})", on_timeout)
    start_stop_poller(job_id, lambda: state["running"], on_manual_stop)

    db = Session(engine)
    success = False
    try:
        if name == UPLOAD_SCRAPER_ID:
            upload_qualifications_scraper(
                sink, lock, user_id, db, stop_event,
                assessment_ids=payload.get("assessment_ids") or [],
                on_context_ready=on_context_ready,
            )
        else:
            SCRAPER_FUNCS[name](
                sink, lock, user_id, db, stop_event,
                on_context_ready=on_context_ready,
            )

        with lock:
            if stop_event.is_set():
                if state["stop_reason"] != "manual":
                    sink.append(json.dumps({"type": "error", "value": "Scraper timed out."}))
            else:
                if name == "cadet-quali":
                    _save_stats_snapshot(db)
                sink.append(json.dumps({"type": "status", "value": "done"}))
                success = True
    except Exception as e:
        log(f"job {job_id} ({name}) CRASH:\n" + traceback.format_exc())
        with lock:
            if stop_event.is_set() and state["stop_reason"] != "manual":
                sink.append(json.dumps({"type": "error", "value": "Scraper timed out."}))
            elif not stop_event.is_set():
                sink.append(json.dumps({"type": "error", "value": f"Crash: {type(e).__name__}: {str(e)}"}))
    finally:
        state["running"] = False
        quit_context()
        db.close()

        if state["stop_reason"] == "manual":
            sink.append(json.dumps({"type": "status", "value": "stopped"}))
            sink.close()
            # A manually stopped run was never recorded as a ScraperRun, so a
            # deliberate cancel doesn't show up as a failure in /api-logs.
            _finish(job_id, "cancelled")
        else:
            sink.close()
            _record_run(name, success, ran_by, sink.messages)
            _finish(job_id, "done" if success else "failed")


# ── Stale-job recovery ────────────────────────────────────────────────────────

def recover_stale_jobs(live_ids: set[int]) -> None:
    """Free jobs left claimed/running by a worker that died.

    Without this a killed worker wedges its scraper forever: the partial unique
    index sees a live row and refuses every new run of that scraper.
    """
    cutoff = datetime.now() - timedelta(seconds=STALE_JOB_SECONDS)
    db = SessionLocal()
    try:
        candidates = (
            db.query(ScraperJob)
            .filter(ScraperJob.status.in_(("claimed", "running")))
            .all()
        )
        for job in candidates:
            if job.id in live_ids:
                continue
            last_log_ts = (
                db.query(ScraperJobLog.ts)
                .filter(ScraperJobLog.job_id == job.id)
                .order_by(ScraperJobLog.seq.desc())
                .limit(1)
                .scalar()
            )
            last_activity = last_log_ts or job.claimed_at or job.requested_at
            if last_activity and last_activity > cutoff:
                continue

            if job.attempts >= MAX_ATTEMPTS:
                job.status = "failed"
                job.finished_at = datetime.now()
                note = f"abandoned after {job.attempts} attempt(s)"
            else:
                job.status = "queued"
                job.worker_id = None
                job.claimed_at = None
                note = f"requeued (attempt {job.attempts + 1} of {MAX_ATTEMPTS})"
            db.commit()
            log(f"job {job.id} ({job.scraper_id}): stale, {note}")
    except SQLAlchemyError as e:
        db.rollback()
        log(f"stale-job sweep failed: {e}")
    finally:
        db.close()


def release_jobs(job_ids: set[int]) -> None:
    """Hand jobs back on a clean shutdown so a deploy doesn't wedge a scraper.

    A restarted worker picks them straight back up rather than waiting out the
    stale sweep.
    """
    if not job_ids:
        return
    db = SessionLocal()
    try:
        for job in db.query(ScraperJob).filter(ScraperJob.id.in_(job_ids)).all():
            if job.status not in ("claimed", "running"):
                continue
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "failed"
                job.finished_at = datetime.now()
            else:
                job.status = "queued"
                job.worker_id = None
                job.claimed_at = None
        db.commit()
        log(f"released {len(job_ids)} in-flight job(s) on shutdown")
    except SQLAlchemyError as e:
        db.rollback()
        log(f"could not release in-flight jobs: {e}")
    finally:
        db.close()


# ── Schedules ─────────────────────────────────────────────────────────────────
# Kept on the worker deliberately: a cloud outage pauses nothing here, and the
# scheduled scrape still runs.

scheduler = BackgroundScheduler()
_schedule_signature: str | None = None


def enqueue_scheduled(name: str) -> None:
    db = SessionLocal()
    try:
        sched = db.query(ScraperSchedule).filter(ScraperSchedule.scraper_id == name).first()
        if not sched or not sched.enabled or not sched.user:
            return
        if not sched.user.bader_credentials:
            log(f"scheduled {name}: {sched.user.email} has no Bader credentials saved, skipping")
            return

        db.add(ScraperJob(
            scraper_id=name,
            status="queued",
            payload={"user_id": sched.user.id},
            requested_by=f"schedule ({sched.user.email})",
            requested_at=datetime.now(),
        ))
        db.commit()
        log(f"scheduled {name}: queued")
    except IntegrityError:
        # The partial unique index — this scraper is already queued or running.
        db.rollback()
        log(f"scheduled {name}: already queued or running, skipping")
    except SQLAlchemyError as e:
        db.rollback()
        log(f"scheduled {name}: could not queue: {e}")
    finally:
        db.close()


def refresh_schedules() -> None:
    """Re-read the schedule rows and re-register cron jobs when they change.

    The API used to call register_schedule_jobs() in-process after a PUT; it
    now only writes the row, so the worker notices it here instead.
    """
    global _schedule_signature
    db = SessionLocal()
    try:
        schedules = {s.scraper_id: s for s in db.query(ScraperSchedule).all()}
        signature = json.dumps(
            {
                name: (
                    bool(s.enabled), s.days_of_week, s.hour, s.minute,
                    s.user_id,
                )
                for name, s in sorted(schedules.items())
            },
            sort_keys=True,
        )
        if signature == _schedule_signature:
            return
        _schedule_signature = signature

        for name in NAMED_SCRAPERS:
            job_id = f"scraper-sched-{name}"
            sched = schedules.get(name)
            if sched and sched.enabled and sched.days_of_week:
                scheduler.add_job(
                    enqueue_scheduled,
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
        log("schedules reloaded")
    except SQLAlchemyError as e:
        log(f"could not read schedules: {e}")
    finally:
        db.close()


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log(f"starting as {WORKER_ID} (max {MAX_CONCURRENT_JOBS} concurrent jobs)")

    def handle_signal(signum, _frame):
        log(f"signal {signum} received, finishing up")
        _shutdown.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    refresh_schedules()
    scheduler.add_job(refresh_schedules, "interval", seconds=SCHEDULE_REFRESH_SECONDS)
    scheduler.start()

    live: dict[int, threading.Thread] = {}
    last_sweep = 0.0

    try:
        while not _shutdown.is_set():
            for job_id in [i for i, t in live.items() if not t.is_alive()]:
                live.pop(job_id)

            now = time.monotonic()
            if now - last_sweep > STALE_JOB_SECONDS / 3:
                recover_stale_jobs(set(live))
                last_sweep = now

            if len(live) < MAX_CONCURRENT_JOBS:
                # The RAM guard moved here from the API endpoint: this is the
                # process that actually has to fit another Chromium in memory,
                # and the job simply waits in the queue instead of 503-ing at
                # whoever pressed the button.
                ram_ok, available_mb = check_ram_ok()
                if not ram_ok:
                    log(f"RAM too low ({available_mb:.0f} MB free), not claiming work")
                else:
                    job = claim_job()
                    if job is not None:
                        log(f"claimed job {job.id} ({job.scraper_id}) for {job.requested_by}")
                        thread = threading.Thread(
                            target=run_job, args=(job,), name=f"job-{job.id}", daemon=True
                        )
                        live[job.id] = thread
                        thread.start()
                        continue  # look for more work straight away

            _shutdown.wait(POLL_SECONDS)
    finally:
        scheduler.shutdown(wait=False)
        # Give in-flight runs a moment to notice the shutdown, then hand back
        # whatever is still going so the next worker can pick it up.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(t.is_alive() for t in live.values()):
            time.sleep(0.5)
        release_jobs({i for i, t in live.items() if t.is_alive()})
        log("stopped")


if __name__ == "__main__":
    main()
