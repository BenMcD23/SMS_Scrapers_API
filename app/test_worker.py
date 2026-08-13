"""Self-check for the home worker's queue mechanics.

Not the scrapers themselves — those need Bader and a browser. What's covered
here is the machinery that replaced running them in the API process: claiming a
job atomically, the log sink the scraper scripts write through unchanged, and
recovering jobs from a worker that died mid-run (which would otherwise wedge a
scraper forever behind the partial unique index).
"""
import json
import threading
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import ScraperJob, ScraperJobLog
import worker as w


class _TestDb:
    """Point the worker's module-level session factory at a scratch database."""

    def __enter__(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._saved = (w.SessionLocal, w.engine)
        w.SessionLocal, w.engine = self.Session, self.engine
        return self.Session()

    def __exit__(self, *_):
        w.SessionLocal, w.engine = self._saved


def _queue(db, scraper_id="cadet-quali", **kw):
    job = ScraperJob(
        scraper_id=scraper_id, status="queued", requested_at=datetime.now(),
        requested_by="staff@317atc.co.uk", **kw,
    )
    db.add(job)
    db.commit()
    return job


def test_claiming_takes_the_oldest_job_once():
    with _TestDb() as db:
        first = _queue(db, "cadet-quali")
        second = _queue(db, "medical")
        second.requested_at = first.requested_at + timedelta(seconds=5)
        db.commit()

        claimed = w.claim_job()
        assert claimed.id == first.id
        assert claimed.status == "claimed"
        assert claimed.worker_id == w.WORKER_ID
        # attempts is what stops a job that kills the worker being requeued
        # forever, so it has to count the claim, not the completion.
        assert claimed.attempts == 1

        assert w.claim_job().id == second.id
        assert w.claim_job() is None


def test_log_sink_writes_what_the_scrapers_append():
    with _TestDb() as db:
        job = _queue(db)
        sink = w.DbLogSink(job.id)
        lock = threading.Lock()

        # Exactly how every scraper in scripts/ logs — unchanged by this move.
        with lock:
            sink.append(json.dumps({"type": "info", "value": "Logged in"}))
            sink.append(json.dumps({"type": "warning", "value": "No PDFs stored"}))
        # ...and how a couple of the older helpers do it.
        sink.append("Pushing data to sheets")
        sink.close()

        rows = db.query(ScraperJobLog).order_by(ScraperJobLog.seq).all()
        assert [(r.seq, r.type, r.value) for r in rows] == [
            (1, "info", "Logged in"),
            (2, "warning", "No PDFs stored"),
            (3, "info", "Pushing data to sheets"),
        ]
        # The watchdog measures progress as log growth, so len() must track it.
        assert len(sink) == 3
        # And the raw payloads survive for the ScraperRun summary.
        assert w._format_run_logs(sink.messages).splitlines()[0] == "Logged in"


def test_stale_job_from_a_dead_worker_is_requeued():
    with _TestDb() as db:
        job = _queue(db)
        job.status = "running"
        job.attempts = 1
        job.claimed_at = datetime.now() - timedelta(seconds=w.STALE_JOB_SECONDS + 60)
        db.commit()

        w.recover_stale_jobs(live_ids=set())

        db.expire_all()
        assert job.status == "queued"
        assert job.worker_id is None
        # Re-claimable rather than wedged: the whole point of the sweep.
        assert w.claim_job().id == job.id


def test_stale_job_is_abandoned_once_it_has_used_up_its_attempts():
    with _TestDb() as db:
        job = _queue(db)
        job.status = "running"
        job.attempts = w.MAX_ATTEMPTS
        job.claimed_at = datetime.now() - timedelta(seconds=w.STALE_JOB_SECONDS + 60)
        db.commit()

        w.recover_stale_jobs(live_ids=set())

        db.expire_all()
        assert job.status == "failed"
        assert job.finished_at is not None


def test_a_live_job_is_never_swept():
    with _TestDb() as db:
        job = _queue(db)
        job.status = "running"
        job.claimed_at = datetime.now() - timedelta(seconds=w.STALE_JOB_SECONDS + 60)
        db.commit()

        # Old claimed_at, but this worker is still running it — a slow scrape
        # must not be mistaken for a dead one.
        w.recover_stale_jobs(live_ids={job.id})
        db.expire_all()
        assert job.status == "running"


def test_a_job_logging_slowly_is_not_stale():
    with _TestDb() as db:
        job = _queue(db)
        job.status = "running"
        job.claimed_at = datetime.now() - timedelta(seconds=w.STALE_JOB_SECONDS + 60)
        db.add(ScraperJobLog(job_id=job.id, seq=1, ts=datetime.now(), type="info", value="alive"))
        db.commit()

        w.recover_stale_jobs(live_ids=set())
        db.expire_all()
        assert job.status == "running"


def test_shutdown_hands_in_flight_jobs_back():
    with _TestDb() as db:
        job = _queue(db)
        job.status = "running"
        job.attempts = 1
        db.commit()

        w.release_jobs({job.id})

        db.expire_all()
        # A deploy restarts the worker mid-run; the next one picks it straight
        # back up instead of waiting out the stale sweep.
        assert job.status == "queued"
        assert job.claimed_at is None
