"""Scraper control plane: starting/stopping named scrapers and upload jobs, the
run log they leave, schedules and the attachment-check list. The scrapers
themselves (Playwright against Bader) are replaced with fakes — this is about
the bookkeeping around them, which is what breaks silently."""

import json
import threading
from datetime import datetime, timedelta

import pytest

import routers.scrapers as sc
from database.models import AttachmentCheckQual, BaderCredentials, ScraperRun, ScraperSchedule, User

STAFF = {"Authorization": "Bearer staff"}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Named-scraper and upload-job state is module-global; give each test its own."""
    states = {
        name: {"messages": [], "lock": threading.Lock(), "running": False, "started_by": None,
               "stop_event": threading.Event(), "stop_reason": None, "run_id": 0, "context": None}
        for name in sc.NAMED_SCRAPERS
    }
    monkeypatch.setattr(sc, "named_scraper_states", states)
    monkeypatch.setattr(sc, "upload_jobs", {})
    # No 15s-sleeping watchdog threads in tests.
    monkeypatch.setattr(sc, "start_watchdog", lambda *a, **k: None)
    import scripts.scraper_utils as su
    monkeypatch.setattr(su, "check_ram_ok", lambda: (True, 4096.0))
    return states


@pytest.fixture
def creds(api, db):
    # The same google_id the fake token carries, so get_or_create_user finds it.
    user = User(google_id="sub-staff", email="staff@317atc.co.uk", first_name="Sam", last_name="Staff")
    db.add(user)
    db.commit()
    db.add(BaderCredentials(user_id=user.id, role_username="u", role_password="p"))
    db.commit()
    return user


def _messages(state):
    return [json.loads(m) for m in state["messages"]]


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/run-scraper/medical"), ("post", "/stop-scraper/medical"), ("get", "/upload-jobs"),
    ("post", "/stop-upload/x"), ("get", "/scrapers-running"), ("get", "/scraper-last-runs"),
    ("get", "/scraper-runs"), ("get", "/scraper-runs/1"), ("get", "/scraper-schedules"),
    ("put", "/scraper-schedules/medical"), ("get", "/attachment-check-quals"), ("put", "/attachment-check-quals"),
])
@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_staff_only(api, method, path, persona):
    call = getattr(api, method)
    res = call(path, headers=api.as_(persona)) if method == "get" else call(path, json={}, headers=api.as_(persona))
    assert res.status_code == 403


def test_api_logs_are_owner_only(api):
    assert api.get("/api-logs", headers=STAFF).status_code == 403
    assert api.get("/api-logs", headers=api.as_("owner")).status_code == 200


@pytest.mark.parametrize("path", ["/scraper-stream/medical", "/upload-stream/x"])
def test_streams_check_the_token_from_header_or_query(api, path):
    assert api.get(path).status_code == 401
    assert api.get(path + "?token=nco").status_code == 403
    assert api.get(path, headers=api.as_("cadet")).status_code == 403


def test_stream_unknown_names(api):
    assert api.get("/scraper-stream/nope?token=staff").status_code == 404
    assert api.get("/upload-stream/nope", headers=STAFF).status_code == 404


# ── starting named scrapers ───────────────────────────────────────────────────

def test_start_needs_credentials(api):
    res = api.get("/run-scraper/medical", headers=STAFF)
    assert res.status_code == 400 and "Settings" in res.json()["detail"]


def test_start_unknown_scraper(api, creds):
    assert api.get("/run-scraper/nope", headers=STAFF).status_code == 404


def test_start_refuses_on_low_ram(api, creds, monkeypatch):
    import scripts.scraper_utils as su
    monkeypatch.setattr(su, "check_ram_ok", lambda: (False, 120.0))
    res = api.get("/run-scraper/medical", headers=STAFF)
    assert res.status_code == 503 and "120 MB" in res.json()["detail"]


def test_successful_run_records_itself(api, db, creds, fresh_state, monkeypatch):
    calls = []

    def fake(messages, lock, user_id, db_, stop_event, on_context_ready=None):
        calls.append(user_id)
        messages.append(json.dumps({"type": "log", "value": "scraped 3 cadets"}))

    monkeypatch.setitem(sc.SCRAPER_FUNCS, "medical", fake)
    assert api.get("/run-scraper/medical", headers=STAFF).json() == {"status": "started"}
    assert calls == [creds.id]

    state = fresh_state["medical"]
    assert state["running"] is False and state["started_by"] is None
    kinds = [(m["type"], m["value"]) for m in _messages(state)]
    assert kinds[0] == ("status", "running") and kinds[-1] == ("status", "done")

    run = db.query(ScraperRun).one()
    assert run.scraper_id == "medical" and run.success is True and run.ran_by == "staff@317atc.co.uk"
    assert "scraped 3 cadets" in run.logs and "[STATUS] done" in run.logs

    last = api.get("/scraper-last-runs", headers=STAFF).json()
    assert last["medical"]["success"] is True and last["staff"]["id"] is None


def test_a_crash_is_logged_and_recorded_as_failed(api, db, creds, fresh_state, monkeypatch):
    def boom(*a, **k):
        raise ValueError("Bader changed its HTML")

    monkeypatch.setitem(sc.SCRAPER_FUNCS, "absences", boom)
    api.get("/run-scraper/absences", headers=STAFF)
    last = _messages(fresh_state["absences"])[-1]
    assert last == {"type": "error", "value": "Crash: Bader changed its HTML"}
    run = db.query(ScraperRun).one()
    assert run.success is False and "[ERROR] Crash" in run.logs
    assert fresh_state["absences"]["running"] is False


def test_timeout_is_reported_and_recorded(api, db, creds, fresh_state, monkeypatch):
    def stalls(messages, lock, user_id, db_, stop_event, on_context_ready=None):
        stop_event.set()  # what the watchdog does

    monkeypatch.setitem(sc.SCRAPER_FUNCS, "staff", stalls)
    api.get("/run-scraper/staff", headers=STAFF)
    assert _messages(fresh_state["staff"])[-1] == {"type": "error", "value": "Scraper timed out."}
    assert db.query(ScraperRun).one().success is False


def test_manual_stop_is_not_recorded_as_a_failure(api, db, creds, fresh_state, monkeypatch):
    def stopped_midway(messages, lock, user_id, db_, stop_event, on_context_ready=None):
        fresh_state["cadet-event"]["stop_reason"] = "manual"
        stop_event.set()
        raise RuntimeError("browser closed")

    monkeypatch.setitem(sc.SCRAPER_FUNCS, "cadet-event", stopped_midway)
    api.get("/run-scraper/cadet-event", headers=STAFF)
    assert _messages(fresh_state["cadet-event"])[-1] == {"type": "status", "value": "stopped"}
    assert db.query(ScraperRun).count() == 0


def test_cadet_quali_takes_a_stats_snapshot(api, db, creds, monkeypatch):
    from database.models import StatsSnapshot

    monkeypatch.setitem(sc.SCRAPER_FUNCS, "cadet-quali", lambda *a, **k: None)
    api.get("/run-scraper/cadet-quali", headers=STAFF)
    assert db.query(StatsSnapshot).count() == 1


def test_cannot_start_twice(api, creds, fresh_state):
    fresh_state["medical"]["running"] = True
    res = api.get("/run-scraper/medical", headers=STAFF)
    assert res.status_code == 400 and "already running" in res.json()["detail"]


def test_stop(api, fresh_state):
    assert api.post("/stop-scraper/nope", headers=STAFF).status_code == 404
    assert api.post("/stop-scraper/medical", headers=STAFF).status_code == 400

    closed = []

    class Ctx:
        def close(self):
            closed.append(True)

    state = fresh_state["medical"]
    state.update(running=True, context=Ctx())
    assert api.post("/stop-scraper/medical", headers=STAFF).json() == {"status": "stopping"}
    assert state["stop_event"].is_set() and state["stop_reason"] == "manual"
    assert closed == [True] and state["context"] is None
    assert "manually stopped" in _messages(state)[-1]["value"]


def test_quit_context_swallows_close_errors():
    class Broken:
        def close(self):
            raise RuntimeError("already gone")

    state = {"context": Broken()}
    sc._quit_context(state)
    assert state["context"] is None


def test_scrapers_running_summary(api, fresh_state):
    fresh_state["staff"].update(running=True, started_by="x@y")
    body = api.get("/scrapers-running", headers=STAFF).json()
    assert body["staff"] == {"running": True, "started_by": "x@y"}
    assert body["medical"]["running"] is False and body["upload_jobs"] == []


# ── upload jobs ───────────────────────────────────────────────────────────────

def test_upload_job_lifecycle(api, db, monkeypatch):
    seen = {}

    def fake_upload(messages, lock, user_id, db_, stop_event, assessment_ids=None, on_context_ready=None):
        seen["ids"] = assessment_ids
        messages.append(json.dumps({"type": "log", "value": "uploaded"}))

    monkeypatch.setattr(sc, "upload_qualifications_scraper", fake_upload)
    job_id, state = sc.create_upload_job("staff@317atc.co.uk")
    assert len(job_id) == 8 and state["running"] is True

    jobs = api.get("/upload-jobs", headers=STAFF).json()
    assert jobs[0]["job_id"] == job_id and jobs[0]["finished_at"] is None

    sc.run_upload_job(job_id, 1, "staff@317atc.co.uk", [4, 5])
    assert seen["ids"] == [4, 5]
    assert state["running"] is False and state["finished_at"] is not None
    assert _messages(state)[-1] == {"type": "status", "value": "done"}
    run = db.query(ScraperRun).one()
    assert run.scraper_id == "upload-qualifications" and run.success is True

    assert api.post(f"/stop-upload/{job_id}", headers=STAFF).status_code == 400
    assert api.post("/stop-upload/nope", headers=STAFF).status_code == 404


def test_upload_job_crash_and_timeout(db, monkeypatch):
    def crash(*a, **k):
        raise KeyError("row")

    monkeypatch.setattr(sc, "upload_qualifications_scraper", crash)
    job_id, state = sc.create_upload_job("s")
    sc.run_upload_job(job_id, 1, "s", [1])
    assert _messages(state)[-1]["value"].startswith("Crash: KeyError")

    def timeout(messages, lock, user_id, db_, stop_event, **k):
        stop_event.set()

    monkeypatch.setattr(sc, "upload_qualifications_scraper", timeout)
    job_id, state = sc.create_upload_job("s")
    sc.run_upload_job(job_id, 1, "s", [1])
    assert _messages(state)[-1] == {"type": "error", "value": "Scraper timed out."}
    assert [r.success for r in db.query(ScraperRun).all()] == [False, False]


def test_stop_running_upload(api):
    job_id, state = sc.create_upload_job("s")
    assert api.post(f"/stop-upload/{job_id}", headers=STAFF).json() == {"status": "stopping"}
    assert state["stop_event"].is_set()


def test_create_upload_job_ram_guard(monkeypatch):
    from fastapi import HTTPException

    import scripts.scraper_utils as su

    monkeypatch.setattr(su, "check_ram_ok", lambda: (False, 10.0))
    with pytest.raises(HTTPException) as e:
        sc.create_upload_job("s")
    assert e.value.status_code == 503


# ── logs ──────────────────────────────────────────────────────────────────────

def test_format_run_logs():
    msgs = [json.dumps({"type": "log", "value": "plain"}), json.dumps({"type": "warning", "value": "careful"}),
            "not json", "", json.dumps({"value": "no type"})]
    assert sc._format_run_logs(msgs).split("\n") == ["plain", "[WARNING] careful", "not json", "", "no type"]


def test_run_history_listing_detail_and_limits(api, db):
    now = datetime.now()
    db.add_all([ScraperRun(scraper_id="medical" if i % 2 else "staff", ran_at=now - timedelta(minutes=i),
                           success=True, logs=f"log {i}") for i in range(120)])
    db.commit()
    assert len(api.get("/scraper-runs?limit=500", headers=STAFF).json()) == 100
    medical = api.get("/scraper-runs?scraper_id=medical&limit=5", headers=STAFF).json()
    assert len(medical) == 5 and {r["scraper_id"] for r in medical} == {"medical"}
    assert medical[0]["ran_at"] > medical[-1]["ran_at"]

    detail = api.get(f"/scraper-runs/{medical[0]['id']}", headers=STAFF).json()
    assert detail["logs"].startswith("log ")
    assert api.get("/scraper-runs/99999", headers=STAFF).status_code == 404


def test_api_logs_and_cleanup_honour_retention(api, db):
    old = datetime.now() - timedelta(days=sc.RUN_LOG_RETENTION_DAYS + 1)
    db.add_all([ScraperRun(scraper_id="staff", ran_at=old, success=True),
                ScraperRun(scraper_id="staff", ran_at=datetime.now(), success=True, logs=None)])
    db.commit()
    body = api.get("/api-logs", headers=api.as_("owner")).json()
    assert body["retention_days"] == sc.RUN_LOG_RETENTION_DAYS
    assert len(body["runs"]) == 1 and body["runs"][0]["logs"] == ""

    sc.cleanup_old_run_logs()
    db.expire_all()
    assert db.query(ScraperRun).count() == 1


# ── schedules ─────────────────────────────────────────────────────────────────

def test_schedules_default_off(api):
    body = api.get("/scraper-schedules", headers=STAFF).json()
    assert set(body) == set(sc.NAMED_SCRAPERS)
    assert body["medical"] == {"enabled": False, "days": [], "hour": 22, "minute": 0, "runs_as": None,
                               "updated_by": None, "updated_at": None}


def test_schedule_save_normalises_days_and_registers(api, creds, monkeypatch):
    registered = []
    monkeypatch.setattr(sc, "register_schedule_jobs", lambda: registered.append(True))
    res = api.put("/scraper-schedules/medical", headers=STAFF,
                  json={"enabled": True, "days": ["fri", "mon", "funday", "mon"], "hour": 6, "minute": 30})
    assert res.status_code == 200
    body = res.json()
    assert body["days"] == ["mon", "fri"] and body["runs_as"] == "staff@317atc.co.uk"
    assert registered == [True]


@pytest.mark.parametrize("body,detail", [
    ({"enabled": True, "days": [], "hour": 1, "minute": 0}, "at least one day"),
    ({"enabled": True, "days": ["xyz"], "hour": 1, "minute": 0}, "at least one day"),
    ({"enabled": False, "days": [], "hour": 24, "minute": 0}, "Invalid time"),
    ({"enabled": False, "days": [], "hour": -1, "minute": 0}, "Invalid time"),
    ({"enabled": False, "days": [], "hour": 1, "minute": 60}, "Invalid time"),
])
def test_schedule_validation(api, creds, body, detail):
    res = api.put("/scraper-schedules/medical", json=body, headers=STAFF)
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_schedule_unknown_and_needs_credentials_to_enable(api):
    body = {"enabled": True, "days": ["mon"], "hour": 1, "minute": 0}
    assert api.put("/scraper-schedules/nope", json=body, headers=STAFF).status_code == 404
    res = api.put("/scraper-schedules/medical", json=body, headers=STAFF)
    assert res.status_code == 400 and "Bader credentials" in res.json()["detail"]
    # Disabling doesn't need them.
    assert api.put("/scraper-schedules/medical", json={**body, "enabled": False}, headers=STAFF).status_code == 200


def test_register_schedule_jobs_syncs_the_scheduler(db, session_factory, monkeypatch):
    added, removed = [], []

    class FakeScheduler:
        def add_job(self, func, trigger, args, id, replace_existing):
            added.append((id, str(trigger), str(trigger.timezone)))

        def remove_job(self, job_id):
            removed.append(job_id)
            if job_id == "scraper-sched-staff":
                raise LookupError("no such job")

    monkeypatch.setattr(sc, "scheduler", FakeScheduler())
    monkeypatch.setattr(sc, "SCHEDULER_ENABLED", True)
    db.add(ScraperSchedule(scraper_id="medical", enabled=True, days_of_week="mon,fri", hour=6, minute=5))
    db.add(ScraperSchedule(scraper_id="absences", enabled=True, days_of_week="", hour=6, minute=5))
    db.commit()
    sc.register_schedule_jobs()
    assert [a[0] for a in added] == ["scraper-sched-medical"]
    assert "mon,fri" in added[0][1] and added[0][2] == "Europe/London"
    assert set(removed) == {f"scraper-sched-{n}" for n in sc.NAMED_SCRAPERS} - {"scraper-sched-medical"}


def test_register_schedule_jobs_is_a_no_op_without_the_scheduler(monkeypatch):
    monkeypatch.setattr(sc, "SCHEDULER_ENABLED", False)
    monkeypatch.setattr(sc, "SessionLocal", lambda: pytest.fail("touched the DB"))
    sc.register_schedule_jobs()


def test_scheduled_run_skips_and_runs(db, creds, fresh_state, monkeypatch):
    ran = []
    monkeypatch.setattr(sc, "run_named_scraper_task", lambda name, f, uid, email: ran.append((name, email)))

    sc._run_scheduled_scraper("medical")  # no schedule row
    db.add(ScraperSchedule(scraper_id="medical", enabled=False, user_id=creds.id))
    db.commit()
    sc._run_scheduled_scraper("medical")  # disabled
    assert ran == []

    db.query(ScraperSchedule).update({"enabled": True})
    db.commit()
    fresh_state["medical"]["running"] = True
    sc._run_scheduled_scraper("medical")  # already running
    assert ran == []

    fresh_state["medical"]["running"] = False
    sc._run_scheduled_scraper("medical")
    assert ran == [("medical", "staff@317atc.co.uk")]
    assert fresh_state["medical"]["started_by"] == "schedule (staff@317atc.co.uk)"


def test_scheduled_run_skips_a_user_without_credentials(api, db, fresh_state, monkeypatch):
    ran = []
    monkeypatch.setattr(sc, "run_named_scraper_task", lambda *a: ran.append(a))
    user = User(google_id="sub-staff", email="staff@317atc.co.uk")
    db.add(user)
    db.commit()
    db.add(ScraperSchedule(scraper_id="staff", enabled=True, user_id=user.id))
    db.commit()
    sc._run_scheduled_scraper("staff")
    assert ran == [] and fresh_state["staff"]["running"] is False


# ── attachment-check quals ────────────────────────────────────────────────────

def test_attachment_check_quals_replace_whole_list(api, db):
    assert api.get("/attachment-check-quals", headers=STAFF).json() == {"quals": []}
    res = api.put("/attachment-check-quals", headers=STAFF,
                  json={"quals": [" Radio ", "radio", "", "  ", "First Aid"]}).json()
    assert res == {"quals": ["First Aid", "Radio"]}
    api.put("/attachment-check-quals", headers=STAFF, json={"quals": ["Only"]})
    assert api.get("/attachment-check-quals", headers=STAFF).json() == {"quals": ["Only"]}
    assert db.query(AttachmentCheckQual).count() == 1
    assert api.put("/attachment-check-quals", headers=STAFF, json={}).status_code == 422
