"""Self-check for the scraper job queue.

The queue is what replaced the in-process scraper state when the API moved to
the cloud, so the properties worth pinning down are the ones that used to be
guaranteed by living in one process: only one live run per named scraper,
uploads exempt from that rule, stop reaching a run it can't touch directly, and
log polling returning strictly the tail after a given seq.

Runs on SQLite like the rest of the suite — the partial unique index is
declared for both dialects precisely so this is testable here.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import ScraperJob, ScraperJobLog, ScraperRun
import routers.scrapers as sc


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _idinfo() -> dict:
    return {"sub": "staff", "email": "staff@317atc.co.uk"}


def test_one_live_job_per_named_scraper():
    db = _db()
    first = sc.enqueue_job(db, "cadet-quali", "staff@317atc.co.uk")
    assert first.status == "queued"

    # The database is the lock — a second run of the same scraper is refused
    # even though nothing in this process is tracking the first.
    with pytest.raises(HTTPException) as exc:
        sc.enqueue_job(db, "cadet-quali", "someone.else@317atc.co.uk")
    assert exc.value.status_code == 409

    # A different scraper is unaffected: named scrapers still run in parallel.
    assert sc.enqueue_job(db, "medical", "staff@317atc.co.uk").id != first.id

    # Once the first finishes, the slot frees up.
    first.status = "done"
    first.finished_at = datetime.now()
    db.commit()
    assert sc.enqueue_job(db, "cadet-quali", "staff@317atc.co.uk").id != first.id


def test_upload_jobs_are_exempt_from_the_lock():
    db = _db()
    a = sc.enqueue_job(db, sc.UPLOAD_SCRAPER_ID, "staff@317atc.co.uk", payload={"assessment_ids": [1]})
    b = sc.enqueue_job(db, sc.UPLOAD_SCRAPER_ID, "staff@317atc.co.uk", payload={"assessment_ids": [2]})
    assert {a.status, b.status} == {"queued"}
    assert len(sc.get_upload_jobs(db=db, idinfo=_idinfo())) == 2


def test_stop_cancels_a_job_the_worker_never_claimed():
    db = _db()
    job = sc.enqueue_job(db, "staff", "staff@317atc.co.uk")

    # Nothing has claimed it, so waiting for a worker just to stop immediately
    # would keep the scraper locked; it's cancelled outright instead.
    assert sc.stop_scraper("staff", db=db, idinfo=_idinfo())["status"] == "cancelled"
    assert db.query(ScraperJob).filter(ScraperJob.id == job.id).one().status == "cancelled"

    # And the slot is free again straight away.
    sc.enqueue_job(db, "staff", "staff@317atc.co.uk")


def test_stop_flags_a_claimed_job_for_the_worker_to_notice():
    db = _db()
    job = sc.enqueue_job(db, "absences", "staff@317atc.co.uk")
    job.status = "running"
    db.commit()

    assert sc.stop_scraper("absences", db=db, idinfo=_idinfo())["status"] == "stopping"
    job = db.query(ScraperJob).filter(ScraperJob.id == job.id).one()
    assert job.stop_requested is True
    # Still running: the worker polls the flag and stops itself.
    assert job.status == "running"


def test_stopping_a_scraper_that_is_not_running_is_a_400():
    db = _db()
    with pytest.raises(HTTPException) as exc:
        sc.stop_scraper("medical", db=db, idinfo=_idinfo())
    assert exc.value.status_code == 400


def test_log_polling_returns_only_the_tail():
    db = _db()
    job = sc.enqueue_job(db, "cadet-event", "staff@317atc.co.uk")
    now = datetime.now()
    for seq in range(1, 6):
        db.add(ScraperJobLog(job_id=job.id, seq=seq, ts=now, type="info", value=f"line {seq}"))
    db.commit()

    first = sc.scraper_logs(job.id, after=0, db=db, idinfo=_idinfo())
    assert [row["value"] for row in first["logs"]] == [f"line {i}" for i in range(1, 6)]
    assert first["last_seq"] == 5
    assert first["running"] is True

    # Polling again with the cursor returns nothing new, and keeps the cursor.
    second = sc.scraper_logs(job.id, after=first["last_seq"], db=db, idinfo=_idinfo())
    assert second["logs"] == []
    assert second["last_seq"] == 5

    db.add(ScraperJobLog(job_id=job.id, seq=6, ts=now, type="status", value="done"))
    db.commit()
    third = sc.scraper_logs(job.id, after=second["last_seq"], db=db, idinfo=_idinfo())
    assert [row["value"] for row in third["logs"]] == ["done"]
    assert third["has_more"] is False


def test_a_truncated_log_page_says_so():
    # A client that has been away long enough to fall a whole page behind must
    # not read "job finished" off a page that stopped early.
    db = _db()
    job = sc.enqueue_job(db, "medical", "staff@317atc.co.uk")
    job.status = "done"
    now = datetime.now()
    for seq in range(1, sc.LOG_PAGE_SIZE + 5):
        db.add(ScraperJobLog(job_id=job.id, seq=seq, ts=now, type="info", value=f"line {seq}"))
    db.commit()

    first = sc.scraper_logs(job.id, after=0, db=db, idinfo=_idinfo())
    assert len(first["logs"]) == sc.LOG_PAGE_SIZE
    assert first["has_more"] is True

    rest = sc.scraper_logs(job.id, after=first["last_seq"], db=db, idinfo=_idinfo())
    assert len(rest["logs"]) == 4
    assert rest["has_more"] is False


def test_scrapers_running_reports_the_live_job_per_scraper():
    db = _db()
    quali = sc.enqueue_job(db, "cadet-quali", "boss@317atc.co.uk")
    quali.status = "running"
    upload = sc.enqueue_job(db, sc.UPLOAD_SCRAPER_ID, "staff@317atc.co.uk")
    db.commit()

    state = sc.scrapers_running(db=db, idinfo=_idinfo())
    assert state["cadet-quali"] == {
        "running": True, "started_by": "boss@317atc.co.uk",
        "job_id": quali.id, "status": "running",
    }
    assert state["medical"]["running"] is False
    assert [j["job_id"] for j in state["upload_jobs"]] == [upload.id]


def test_cleanup_drops_old_jobs_and_their_logs():
    db = _db()
    old = ScraperJob(
        scraper_id="medical", status="done",
        requested_at=datetime.now() - timedelta(days=sc.RUN_LOG_RETENTION_DAYS + 1),
        finished_at=datetime.now() - timedelta(days=sc.RUN_LOG_RETENTION_DAYS + 1),
    )
    db.add(old)
    db.commit()
    db.add(ScraperJobLog(job_id=old.id, seq=1, ts=datetime.now(), type="info", value="stale"))
    db.add(ScraperRun(
        scraper_id="medical", ran_at=datetime.now() - timedelta(days=sc.RUN_LOG_RETENTION_DAYS + 1),
        success=True, ran_by="staff@317atc.co.uk", logs="stale",
    ))
    recent = sc.enqueue_job(db, "staff", "staff@317atc.co.uk")
    db.commit()

    # cleanup_old_run_logs opens its own session against the app engine, so
    # point that at this test database for the call.
    original = sc.SessionLocal
    sc.SessionLocal = sessionmaker(bind=db.get_bind())
    try:
        sc.cleanup_old_run_logs()
    finally:
        sc.SessionLocal = original

    assert db.query(ScraperRun).count() == 0
    assert [j.id for j in db.query(ScraperJob).all()] == [recent.id]
    assert db.query(ScraperJobLog).count() == 0


def test_run_logs_keep_the_format_the_run_history_reads_back():
    # The worker hands _format_run_logs the same JSON payloads the scrapers
    # appended, so the stored text must be byte-identical to the old buffer's.
    import json
    messages = [
        json.dumps({"type": "info", "value": "Logged in"}),
        json.dumps({"type": "warning", "value": "No PDFs stored"}),
        "Pushing data to sheets",
    ]
    assert sc._format_run_logs(messages) == (
        "Logged in\n[WARNING] No PDFs stored\nPushing data to sheets"
    )
