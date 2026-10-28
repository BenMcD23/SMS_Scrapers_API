"""What each scraper run writes to the database, with Bader faked out.

The page-reading functions are replaced by canned data, so these pin the sync
rules themselves: upsert-not-duplicate, never wipe on a failed read, remove
cadets who have left, dedupe qualifications, and match names to people.
"""

import json
import threading
from datetime import date, datetime

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
    CadetLeavingProcess,
    CadetMedical,
    CadetQualification,
    CadetWht,
    FlyingEvent,
    RemovedCadet,
    StoresOrder,
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
    monkeypatch.setattr(calls, "get_cadet_names", lambda page: (["x"], 1, {}))
    monkeypatch.setattr(calls, "get_parent_emails", lambda page: [])
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


@pytest.mark.parametrize("unread", [None, []])
def test_whts_are_replaced_only_when_something_was_read(db, bader, monkeypatch, unread):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.add(CadetWht(cadet_id=1001, weapon="L98A2", date_passed=date(2025, 1, 1), date_expires=date(2025, 7, 1)))
    db.commit()
    # A tab that failed to load (None) or read nothing ([]) keeps the old tests.
    _quali(monkeypatch, [_amy(whts=unread)])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.query(CadetWht).count() == 1

    wht = {"weapon": "Scorpion (single shot)", "date_passed": date(2026, 3, 28),
           "date_expires": date(2026, 9, 28), "awarded_by": "Doherty, S Flt Lt"}
    _quali(monkeypatch, [_amy(whts=[wht, {**wht, "weapon": "L98A2"}])])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert sorted(w.weapon for w in db.query(CadetWht).all()) == ["L98A2", "Scorpion (single shot)"]
    assert "2 WHT(s)" in _summary(run)


def _summary(run):
    return next(m["value"] for m in run.parsed() if "DB update complete" in str(m.get("value")))


def test_a_cadet_not_on_sms_is_flagged_never_deleted(db, bader, monkeypatch):
    db.add_all([Cadet(cin=1001, first_name="Amy", last_name="Able"),
                Cadet(cin=2002, first_name="Gone", last_name="Away")])
    db.add(CadetQualification(cadet_id=2002, qual_type="Radio", status="true"))
    db.add(StoresOrder(id=5, cadet_id=2002, created_at=datetime.now()))
    db.commit()
    _quali(monkeypatch, [_amy()])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    gone = db.get(Cadet, 2002)
    assert gone.missing_since is not None and db.get(Cadet, 1001).missing_since is None
    assert db.query(CadetQualification).count() == 1 and db.query(StoresOrder).count() == 1
    assert "1 no longer on SMS" in _summary(run)


def test_a_later_run_keeps_the_first_missing_date(db, bader, monkeypatch):
    # The banner says how long they've been gone, so later misses mustn't reset it.
    first = datetime(2026, 10, 1)
    db.add_all([Cadet(cin=1001, first_name="Amy", last_name="Able"),
                Cadet(cin=2002, first_name="Gone", last_name="Away", missing_since=first)])
    db.commit()
    _quali(monkeypatch, [_amy()])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert db.get(Cadet, 2002).missing_since == first


def test_a_cadet_seen_again_is_unflagged(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able", missing_since=datetime(2026, 10, 1)))
    db.commit()
    _quali(monkeypatch, [_amy()])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert db.get(Cadet, 1001).missing_since is None
    assert "0 no longer on SMS" in _summary(run)


@pytest.mark.parametrize("bad_cin", [None, "", "abc"])
def test_a_cadet_whose_cin_wasnt_read_is_not_flagged_as_gone(db, bader, monkeypatch, bad_cin):
    # Prod 2026-10-16: Finley Kay's profile hadn't loaded when the CIN was read,
    # so he was skipped, then deleted (with his orders) as having left.
    db.add_all([Cadet(cin=1001, first_name="Amy", last_name="Able"),
                Cadet(cin=3003, first_name="Finley", last_name="Kay")])
    db.commit()
    _quali(monkeypatch, [_amy(), {"cin": bad_cin, "first_name": "Finley", "last_name": "Kay"}])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert db.get(Cadet, 3003).missing_since is None
    assert any("[SKIPPED] Finley Kay" in w for w in run.warnings())
    assert any("Not checking for cadets who've left SMS" in w for w in run.warnings())
    assert "1 skipped" in _summary(run) and "0 no longer on SMS" in _summary(run)


def test_a_removed_cadet_still_on_bader_is_not_added_back(db, bader, monkeypatch):
    db.add(RemovedCadet(cin=1001, first_name="Amy", last_name="Able", removed_at=datetime.now()))
    db.commit()
    _quali(monkeypatch, [_amy()])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.get(Cadet, 1001) is None and run.errors() == []


def test_an_empty_scrape_flags_nobody(db, bader, monkeypatch):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.commit()
    _quali(monkeypatch, [])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert db.get(Cadet, 1001).missing_since is None
    assert calls.mark_missing_cadets(db, set()) == 0


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


# ── parent emails (end of the cadet scrape) ───────────────────────────────────

def _nok(cin, first, last, email):
    return {"cin": cin, "first_name": first, "last_name": last, "email": email}


def test_parent_emails_from_bader_are_added_to_the_ones_the_oc_typed(db, bader, monkeypatch):
    db.add_all([
        Cadet(cin=1001, first_name="Amy", last_name="Able", parent_emails="typed@x.com"),
        Cadet(cin=1002, first_name="Bob", last_name="Baker"),
        Cadet(cin=1003, first_name="Cat", last_name="Cole", parent_emails="kept@x.com"),
    ])
    db.commit()
    _quali(monkeypatch, [_amy(), _amy(cin="1002", first_name="Bob", last_name="Baker"),
                         _amy(cin="1003", first_name="Cat", last_name="Cole")])
    monkeypatch.setattr(calls, "get_parent_emails", lambda page: [
        _nok(1001, "Amy", "Able", "mum@x.com"),
        _nok(1001, "Amy", "Able", "TYPED@x.com"),       # already there, other case
        _nok(1002, "Robert", "Baker", "dad@x.com"),     # matched on CIN, not the name
        _nok(1002, "Robert", "Baker", "not an address"),
        _nok(1003, "Cat", "Cole", ""),                  # NoK without an email
        _nok(9999, "Ghost", "Nobody", "ghost@x.com"),
        _nok(None, "No", "Cin", "nocin@x.com"),
    ])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    assert db.get(Cadet, 1001).parent_emails == "typed@x.com;mum@x.com"
    assert db.get(Cadet, 1002).parent_emails == "dad@x.com"
    assert db.get(Cadet, 1003).parent_emails == "kept@x.com"   # nothing new: untouched
    assert run.errors() == []
    warnings = run.warnings()
    assert any("[NO MATCH] Ghost Nobody (CIN 9999)" in w for w in warnings)
    assert any("[NO MATCH] No Cin (no CIN)" in w for w in warnings)
    assert not any("ghost@x.com" in w or "nocin@x.com" in w for w in warnings)
    # An invalid address is reported by cadet, never echoed into the run log.
    assert any("Robert Baker is not a valid address" in w for w in warnings)
    assert not any("not an address" in w for w in warnings)


def test_scraped_parent_emails_are_what_the_oc_leaving_page_offers(api, db, bader, monkeypatch):
    # Amy has been off parade nine weeks, so she's on the leaving list.
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able"))
    db.add_all([
        CadetAttendance(cadet_id=1001, date=datetime(2026, 7, 29), status="Present", register_type="Parade Night"),
        CadetAttendance(cadet_id=1001, date=datetime(2026, 9, 30), status="Absent", register_type="Parade Night"),
    ])
    db.commit()
    _quali(monkeypatch, [_amy()])
    monkeypatch.setattr(calls, "get_parent_emails", lambda page: [
        _nok(1001, "Amy", "Able", "mum@x.com"), _nok(1001, "Amy", "Able", "dad@x.com")])
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)

    oc = api.as_("oc")
    [row] = api.get("/leaving-process", headers=oc).json()["cadets"]
    assert (row["cin"], row["parentEmails"]) == (1001, ["mum@x.com", "dad@x.com"])
    assert api.get("/oc/parent-emails", headers=oc).json()["bcc"] == "mum@x.com;dad@x.com"
    # Marking the parents notified records the scraped addresses it went to.
    assert api.post("/leaving-process/1001/notified", headers=oc).status_code == 200
    db.expire_all()
    assert db.query(CadetLeavingProcess).one().sent_to == "mum@x.com;dad@x.com"


@pytest.mark.parametrize("report", [
    lambda page: [],
    lambda page: (_ for _ in ()).throw(calls.PlaywrightTimeoutError("report never drew")),
    lambda page: (_ for _ in ()).throw(RuntimeError("Bader 500")),
])
def test_a_missing_or_empty_report_keeps_parent_emails_and_the_cadet_scrape(db, bader, monkeypatch, report):
    db.add(Cadet(cin=1001, first_name="Amy", last_name="Able", parent_emails="typed@x.com"))
    db.commit()
    _quali(monkeypatch, [_amy(rank="Sgt")])
    monkeypatch.setattr(calls, "get_parent_emails", report)
    run = Run()
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    db.expire_all()
    amy = db.get(Cadet, 1001)
    assert (amy.parent_emails, amy.rank) == ("typed@x.com", "Sgt")
    # A warning, not an error: the run still counts as a success.
    assert run.errors() == []
    assert any("parent emails left as they were" in w for w in run.warnings())


def test_a_stopped_run_does_not_read_the_next_of_kin_report(db, bader, monkeypatch):
    _quali(monkeypatch, [_amy()])
    read = []
    monkeypatch.setattr(calls, "get_parent_emails", lambda page: read.append(page) or [])
    run = Run()
    real_commit = db.commit

    def commit_then_stop():
        real_commit()
        run.stop.set()
    monkeypatch.setattr(db, "commit", commit_then_stop)
    calls.info_and_quali_scraper(run.messages, run.lock, 1, db, run.stop)
    assert read == []


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

def _events(monkeypatch, attendees, links_317=(), details=None, pages=None):
    monkeypatch.setattr(calls, "get_event_names_and_317_links", lambda page: (["e"], 1, list(links_317)))
    monkeypatch.setattr(calls, "get_event_attendees", lambda *a, **k: attendees)
    monkeypatch.setattr(calls, "get_event_details", lambda *a, **k: (details or {}, pages or {}))
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


def test_a_cadet_listed_twice_on_an_event_is_saved_once(db, bader, monkeypatch):
    # Bader can list the same cadet twice (or "A Able" and "Amy Able" both
    # match). One place per cadet per event is a database rule now, so a
    # second insert would fail the whole events scrape.
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.commit()
    _events(monkeypatch, [{"event_name": "Camp", "attendees": [["x", "Amy", "Able"], ["x", "A", "Able"]],
                           "sub_apps": []}])
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.parsed()[-1] == {"type": "status", "value": "done"}
    assert [(ce.event.title, ce.cadet_id) for ce in db.query(CadetEvent).all()] == [("Camp", 1)]


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


def test_event_categories_are_stored_on_every_event_read(db, bader, monkeypatch):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.commit()
    _events(monkeypatch, [
        {"event_name": "March", "event_id": "10", "attendees": [["x", "Amy", "Able"]]},
        {"event_name": "Unread", "event_id": "11", "attendees": [["x", "Amy", "Able"]]},
        {"event_name": "No id", "event_id": None, "attendees": [["x", "Amy", "Able"]]},
    ], details={"10": {"category": "Road Marching"}})
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    rows = {e.title: (e.bader_event_id, e.category) for e in db.query(AllEvent).all()}
    assert rows == {"March": ("10", "Road Marching"), "Unread": ("11", None), "No id": (None, None)}
    assert any("Read categories for 1 of 2" in str(m["value"]) for m in run.parsed())


def test_a_category_read_failure_still_saves_the_events(db, bader, monkeypatch):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.commit()
    _events(monkeypatch, [{"event_name": "Camp", "event_id": "10", "attendees": [["x", "Amy", "Able"]]}])

    def boom(*a, **k):
        raise RuntimeError("bader down")

    monkeypatch.setattr(calls, "get_event_details", boom)
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors() == [] and any("Could not read event categories" in w for w in run.warnings())
    assert [(e.title, e.category) for e in db.query(AllEvent).all()] == [("Camp", None)]


def test_flying_events_are_tracked_with_cadets_from_their_sub_apps(db, bader, monkeypatch):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Able"),
                Cadet(cin=2, first_name="Bob", last_name="Baker")])
    db.commit()
    _events(monkeypatch, [
        {"event_name": "AEF Day", "event_id": "10", "attendees": [["x", "Amy", "Able"]],
         "sub_apps": [{"sub_app_name": "PM slot", "attendees": [["x", "Bob", "Baker"]]}]},
        {"event_name": "March", "event_id": "11", "attendees": [["x", "Amy", "Able"]]},
    ], details={"10": {"category": "RAFAC Air Experience Flying (AEF)", "date_from": "01/11/2026 08:00"},
                "11": {"category": "Road Marching"}})
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    [fe] = db.query(FlyingEvent).all()
    assert (fe.bader_event_id, fe.title, fe.date_from) == ("10", "AEF Day", datetime(2026, 11, 1, 8))
    assert sorted(r.cadet_id for r in fe.cadets) == [1, 2]
    assert any("Flying paperwork: 1 flying event(s)" in str(m["value"]) for m in run.parsed())


def test_a_flying_sync_failure_is_only_a_warning(db, bader, monkeypatch):
    _events(monkeypatch, [{"event_name": "AEF", "event_id": "10", "attendees": []}],
            details={"10": {"category": "RAFAC Gliding"}})

    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(calls, "sync_flying_events", boom)
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert run.errors() == [] and any("Flying paperwork sync failed" in w for w in run.warnings())
    assert run.parsed()[-1] == {"type": "status", "value": "done"}


def test_the_317_sync_reuses_the_pages_the_category_pass_fetched(db, bader, monkeypatch):
    _events(monkeypatch, [], links_317=["link"], pages={"link": "<html/>"})
    seen = {}
    monkeypatch.setattr(calls, "get_317_event_info", lambda *a, **k: seen.update(k))
    run = Run()
    calls.cadet_event_scraper(run.messages, run.lock, 1, db, run.stop)
    assert seen["prefetched"] == {"link": "<html/>"}


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

