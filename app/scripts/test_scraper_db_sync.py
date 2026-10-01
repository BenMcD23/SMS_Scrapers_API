"""What each scraper run writes to the database, with Bader faked out.

The page-reading functions are replaced by canned data, so these pin the sync
rules themselves: upsert-not-duplicate, never wipe on a failed read, remove
cadets who have left, dedupe qualifications, and match names to people.
"""

import json
import threading
from datetime import datetime

import pytest

import scripts.scraper_calls as calls
from database.models import (
    AllEvent,
    BanNotification,
    Cadet,
    CadetAbsence,
    CadetAttendance,
    CadetDietary,
    CadetEvent,
    CadetMedical,
    CadetQualification,
    StoresOrder,
    StoresOrderItem,
)


class Run:
    """One scraper invocation's message log and stop flag."""

    def __init__(self):
        self.messages, self.lock, self.stop = [], threading.Lock(), threading.Event()

    def parsed(self):
        out = []
        for m in self.messages:
            try:
                out.append(json.loads(m))
            except (ValueError, TypeError):
                out.append({"type": "raw", "value": m})
        return out

    def errors(self):
        return [m["value"] for m in self.parsed() if m["type"] == "error"]

    def warnings(self):
        return [m["value"] for m in self.parsed() if m["type"] == "warning"]


@pytest.fixture
def bader(monkeypatch):
    closed = []

    class Ctx:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(calls, "init_scraper", lambda user_id, db: ("page", Ctx(), {}))
    monkeypatch.setattr(calls, "login", lambda *a, **k: None)
    monkeypatch.setattr(calls, "push_to_google_apps_script", lambda *a, **k: None)
    monkeypatch.setattr(calls, "get_cadet_names", lambda page: (["x"], 1, {}))
    monkeypatch.setattr(calls, "get_workspace_users", lambda: [
        {"first_name_key": "AMY", "last_name_key": "ABLE", "email": "amy@317atc.co.uk"}])
    return closed


def _quali(monkeypatch, cadets):
    monkeypatch.setattr(calls, "get_cadet_info_and_qualifications", lambda *a, **k: cadets)


def _amy(**over):
    return {"cin": "1001", "first_name": "Amy", "last_name": "Able", "rank": "Cpl", "flight": "A",
            "classification": "Leading Cadet", "qualifications": [], "attendance": [], **over}


# ── cadet info + qualifications ───────────────────────────────────────────────

def test_new_cadet_is_created_with_matched_email(db, bader, monkeypatch):
    _quali(monkeypatch, [_amy()])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    amy = db.get(Cadet, 1001)
    assert (amy.first_name, amy.rank, amy.email, amy.classification) == ("Amy", "Cpl", "amy@317atc.co.uk",
                                                                         "Leading Cadet")
    assert run.errors() == [] and bader == [True]  # browser closed


def test_blank_scraped_fields_never_overwrite_known_ones(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able", rank="Sgt", flight="B", email="kept@x"))
    db.commit()
    monkeypatch.setattr(calls, "get_workspace_users", lambda: [])
    _quali(monkeypatch, [_amy(rank="", flight=None, classification="")])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    amy = db.get(Cadet, 1001)
    assert (amy.rank, amy.flight, amy.email) == ("Sgt", "B", "kept@x")


def test_qualifications_are_deduped_upserted_and_stale_ones_removed(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.add_all([
        CadetQualification(cadet_id=1001, qual_type="Old Qual", status="true"),
        CadetQualification(cadet_id=1001, qual_type="Radio", status="true", date_achieved=None),
    ])
    db.commit()
    _quali(monkeypatch, [_amy(qualifications=[
        "Plain String Qual",
        {"qual_type": "Radio", "date_achieved": datetime(2025, 1, 1), "has_attachment": False},
        {"qual_type": "First Aid", "date_achieved": datetime(2024, 1, 1)},
        {"qual_type": "First Aid", "date_achieved": datetime(2025, 6, 1), "date_expires": datetime(2028, 6, 1)},
        {"qual_type": ""},
    ])])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    quals = {q.qual_type: q for q in db.query(CadetQualification).all()}
    assert set(quals) == {"Plain String Qual", "Radio", "First Aid"}
    # The newest of two duplicate rows wins.
    assert quals["First Aid"].date_achieved == datetime(2025, 6, 1)
    # An existing qual gains the date it was missing and the attachment flag.
    assert quals["Radio"].date_achieved == datetime(2025, 1, 1) and quals["Radio"].has_attachment is False
    assert any("[MISSING ATTACHMENT]" in w and "Radio" in w for w in run.warnings())


def test_a_failed_qualifications_tab_never_wipes_history(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.add(CadetQualification(cadet_id=1001, qual_type="Radio", status="true"))
    db.commit()
    _quali(monkeypatch, [_amy(qualifications=None)])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.query(CadetQualification).count() == 1


def test_attendance_is_replaced_only_when_something_was_read(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.add(CadetAttendance(cadet_id=1001, date=datetime(2025, 1, 1), status="Present"))
    db.commit()
    _quali(monkeypatch, [_amy(attendance=[])])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.query(CadetAttendance).count() == 1

    row = {"date": datetime(2026, 1, 1), "register_type": "Parade Night", "status": "Present", "unit": "317"}
    _quali(monkeypatch, [_amy(attendance=[row, {**row, "date": datetime(2026, 1, 8)}])])
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert sorted(a.date.day for a in db.query(CadetAttendance).all()) == [1, 8]


def test_departed_cadets_are_removed_with_their_records(db, bader, monkeypatch):
    db.add_all([Cadet(cin=1001, first_name="Amy", last_name="Able"),
                Cadet(cin=2002, first_name="Gone", last_name="Away")])
    db.add(CadetQualification(cadet_id=2002, qual_type="Radio", status="true"))
    db.add(StoresOrder(id=5, cadet_id=2002, created_at=datetime.now()))
    db.add(StoresOrderItem(order_id=5, item_type="Beret"))
    db.commit()
    _quali(monkeypatch, [_amy(), {"cin": None, "first_name": "No", "last_name": "Cin"},
                         {"cin": "abc", "first_name": "Bad", "last_name": "Cin"}])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert [c.cin for c in db.query(Cadet).all()] == [1001]
    assert db.query(CadetQualification).count() == 0 and db.query(StoresOrderItem).count() == 0
    summary = next(m["value"] for m in run.parsed() if "DB update complete" in str(m.get("value")))
    assert "2 skipped" in summary and "1 removed" in summary


def test_an_empty_scrape_removes_nobody(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.commit()
    _quali(monkeypatch, [])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.query(Cadet).count() == 1
    assert calls.remove_departed_cadets(db, set()) == 0


def test_workspace_lookup_failure_is_a_warning_not_a_failure(db, bader, monkeypatch):
    def down():
        raise RuntimeError("directory API quota")

    monkeypatch.setattr(calls, "get_workspace_users", down)
    _quali(monkeypatch, [_amy()])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.get(Cadet, 1001).email is None
    assert run.errors() == [] and any("Continuing without emails" in w for w in run.warnings())


def test_stop_before_saving_writes_nothing(db, bader, monkeypatch):
    run = Run()

    def stop_midway(*a, **k):
        run.stop.set()
        return [_amy()]

    monkeypatch.setattr(calls, "get_cadet_info_and_qualifications", stop_midway)
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.query(Cadet).count() == 0 and bader == [True]


def test_a_crash_is_reported_rolled_back_and_the_browser_closed(db, bader, monkeypatch):
    def boom(page):
        raise RuntimeError("selector not found")

    monkeypatch.setattr(calls, "get_cadet_names", boom)
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors() == ["Scraper Error: selector not found"] and bader == [True]


def test_a_playwright_timeout_is_reported_as_such(db, bader, monkeypatch):
    def slow(page):
        raise calls.PlaywrightTimeoutError("30000ms exceeded")

    monkeypatch.setattr(calls, "get_cadet_names", slow)
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors() == ["A page took too long to load (Timeout)."]


# ── absences ──────────────────────────────────────────────────────────────────

def test_absences_full_replace_and_name_matching(db, bader, monkeypatch):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Able"),
                Cadet(cin=2, first_name="Bob", last_name="Baker")])
    db.add(CadetAbsence(cadet_id=2, date_from=datetime(2025, 1, 1), date_to=datetime(2025, 1, 2),
                        scraped_at=datetime(2025, 1, 1)))
    db.commit()
    monkeypatch.setattr(calls, "get_absences", lambda page: [
        {"first_name": "Amy", "last_name": "Able", "date_from": datetime(2026, 3, 1),
         "date_to": datetime(2026, 3, 5), "reason": ""},
        {"first_name": "A.", "last_name": "Able", "date_from": datetime(2026, 4, 1),
         "date_to": datetime(2026, 4, 1), "reason": "Exams"},
        {"first_name": "Ghost", "last_name": "Nobody", "date_from": datetime(2026, 3, 1),
         "date_to": datetime(2026, 3, 1), "reason": None},
    ])
    run = Run()
    calls.absence_scraper(run.messages, run.lock, 1, db, run.stop)
    rows = db.query(CadetAbsence).order_by(CadetAbsence.date_from).all()
    assert [(r.cadet_id, r.reason) for r in rows] == [(1, None), (1, "Exams")]
    assert any("[NO MATCH]" in w and "Ghost" in w for w in run.warnings())


def test_absence_crash_keeps_the_old_rows(db, bader, monkeypatch):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.add(CadetAbsence(cadet_id=1, date_from=datetime(2026, 1, 1), date_to=datetime(2026, 1, 2),
                        scraped_at=datetime(2026, 1, 1)))
    db.commit()
    monkeypatch.setattr(calls, "get_absences", lambda page: [{"first_name": "Amy"}])  # malformed row
    run = Run()
    calls.absence_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors() and db.query(CadetAbsence).count() == 1


# ── events and ban alerts ─────────────────────────────────────────────────────

def _events(monkeypatch, attendees, links_317=()):
    monkeypatch.setattr(calls, "get_event_names_and_317_links", lambda page: (["e"], 1, list(links_317)))
    monkeypatch.setattr(calls, "get_event_attendees", lambda *a, **k: attendees)
    monkeypatch.setattr(calls, "get_317_event_info", lambda *a, **k: None)


def test_events_and_sub_apps_are_rebuilt_with_matched_cadets(db, bader, monkeypatch):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Able"),
                Cadet(cin=2, first_name="Bob", last_name="Baker")])
    db.add(AllEvent(id=99, title="Stale event"))
    db.commit()
    _events(monkeypatch, [
        {"event_name": "Camp", "attendees": [["x", "Amy", "Able"], ["x", "B", "Baker"], ["x", "Who", "Ever"],
                                             "junk", ["short"]],
         "sub_apps": [{"sub_app_name": "Camp – Week 2", "attendees": [["x", "Bob", "Baker"]]},
                      {"sub_app_name": "Empty", "attendees": None}]},
        {"event_name": "Nothing read", "attendees": None, "sub_apps": []},
    ])
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    titles = sorted(e.title for e in db.query(AllEvent).all())
    assert titles == ["Camp", "Camp – Week 2"]
    pairs = sorted((ce.event.title, ce.cadet_id) for ce in db.query(CadetEvent).all())
    assert pairs == [("Camp", 1), ("Camp", 2), ("Camp – Week 2", 2)]
    assert any("1 attendee(s) could not be matched" in str(m["value"]) for m in run.parsed())
    assert run.parsed()[-1] == {"type": "status", "value": "done"}


def test_a_failed_event_save_keeps_the_previous_events(db, bader, monkeypatch):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.add(AllEvent(id=1, title="Existing"))
    db.add(CadetEvent(event_id=1, cadet_id=1))
    db.commit()
    _events(monkeypatch, [{"event_name": None, "attendees": [["x", "Amy", "Able"]]}])  # title can't be null
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors()
    db.expire_all()
    assert [e.title for e in db.query(AllEvent).all()] == ["Existing"]
    assert db.query(CadetEvent).count() == 1


def test_banned_cadets_on_events_are_emailed_once(db, bader, monkeypatch, outbox):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Able", banned=True),
                Cadet(cin=2, first_name="Bob", last_name="Baker")])
    db.commit()
    attendees = [{"event_name": "Camp", "attendees": [["x", "Amy", "Able"], ["x", "Bob", "Baker"]]}]
    _events(monkeypatch, attendees)
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert len(outbox) == 1 and "Amy Able" in outbox[0]["html"] and "Bob" not in outbox[0]["html"]
    assert db.query(BanNotification).count() == 1

    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert len(outbox) == 1


def test_check_ban_notifications_with_nobody_banned(db):
    assert calls.check_ban_notifications(db) == 0


def test_317_detail_sync_failure_is_only_a_warning(db, bader, monkeypatch):
    _events(monkeypatch, [], links_317=["link"])

    def boom(*a, **k):
        raise RuntimeError("detail page changed")

    monkeypatch.setattr(calls, "get_317_event_info", boom)
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors() == [] and any("detail sync failed" in w for w in run.warnings())


# ── medical ───────────────────────────────────────────────────────────────────

def test_medical_replaces_per_cadet_and_skips_unknowns(db, bader, monkeypatch):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.add(CadetMedical(cadet_id=1, allergy_name="Old"))
    db.commit()
    monkeypatch.setattr(calls, "get_cadet_medical", lambda *a, **k: [
        {"cin": 1, "cadet_name": "Amy Able",
         "allergies": [{"allergy": "Nuts", "auto_injector": "Yes", "severity": "High"}],
         "dietary_restrictions": [{"name": "Vegan"}]},
        {"cin": None, "cadet_name": "No Cin"},
        {"cin": 99, "cadet_name": "Not Here"},
    ])
    run = Run()
    calls.medical_scraper(run.messages, run.lock, 1, db, run.stop)
    assert [(m.allergy_name, m.auto_injector) for m in db.query(CadetMedical).all()] == [("Nuts", "Yes")]
    assert [d.name for d in db.query(CadetDietary).all()] == ["Vegan"]
    assert len(run.warnings()) == 2
    assert run.parsed()[-1] == {"type": "status", "value": "done"}


# ── sheets push ───────────────────────────────────────────────────────────────

def test_sheets_push_failures_are_warnings(monkeypatch):
    import requests

    import scripts.scraper_utils as su

    run = Run()
    seen = {}

    def ok(url, json, headers, timeout):
        seen["timeout"] = timeout
        return type("R", (), {"status_code": 200, "text": "ok"})()

    monkeypatch.setattr(su.requests, "post", ok)
    su.push_to_google_apps_script({"a": 1}, "https://x", run.messages, run.lock)
    assert seen["timeout"] and run.warnings() == []

    monkeypatch.setattr(su.requests, "post", lambda *a, **k: type("R", (), {"status_code": 500, "text": "boom"})())
    su.push_to_google_apps_script({}, "https://x", run.messages, run.lock)

    def down(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(su.requests, "post", down)
    su.push_to_google_apps_script({}, "https://x", run.messages, run.lock)
    assert run.errors() == [] and len(run.warnings()) == 2
