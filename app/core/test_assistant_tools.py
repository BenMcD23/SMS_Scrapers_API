"""The assistant's tools over the rest of the squadron's records: the RAFAC
documents, events, theory, badge orders, the NCO team, session plans, the
leaving process, stats history and staff. The core tools (cadets, attendance,
expiry, stores, aggregate) are in test_assistant.py."""

import json
from datetime import datetime, timedelta

import httpx
import pytest

import core.assistant_tools as tools
from core.assistant import run_tool
from database.models import (
    AllEvent,
    BadgeOrder,
    BadgeOrderItem,
    Cadet,
    CadetAttendance,
    CadetEvent,
    CadetLeavingProcess,
    CadetTheoryLesson,
    CadetTheoryProgress,
    NcoAppraisal,
    NcoComment,
    SessionPlan,
    Staff,
    StaffAttendance,
    StatsSnapshot,
    User,
)

NOW = datetime.now()


def call(db, tool, **args):
    return run_tool(db, tool, args)


@pytest.fixture
def records(db):
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="Ashworth", rank="corporal", flight="A",
              email="amy@example.com"),
        Cadet(cin=2, first_name="Ben", last_name="Brook", rank="Cadet", flight="B"),
        Cadet(cin=3, first_name="Siân", last_name="Ó Briain", rank="sergeant", flight="B"),
    ])
    author = User(google_id="g1", email="sam@317atc.co.uk", first_name="Sam", last_name="Staff")
    db.add(author)
    camp = AllEvent(title="Summer Camp")
    shoot = AllEvent(title="Range day", category="Shooting", parent=camp)
    db.add_all([camp, shoot, CadetEvent(cadet_id=1, event=camp), CadetEvent(cadet_id=2, event=camp),
                CadetEvent(cadet_id=1, event=shoot)])
    db.add_all([
        CadetTheoryProgress(cadet_id=1, lesson_key="acp_34_2", completed_at=NOW),
        CadetTheoryLesson(cadet_id=2, lesson_key="acp_34_2", part=1, attended_at=NOW),
        CadetTheoryLesson(cadet_id=2, lesson_key="acp_34_2", part=2, attended_at=NOW),
    ])
    order = BadgeOrder(cadet_id=2, created_at=NOW - timedelta(days=5))
    order.order_items = [BadgeOrderItem(badge_name="Radio – Blue", ready_to_collect=NOW),
                         BadgeOrderItem(badge_name="First Class", given_at=NOW)]
    db.add(order)
    db.add(NcoAppraisal(cadet_id=1, author=author, appraisal_date=NOW - timedelta(days=400),
                        next_review_date=NOW - timedelta(days=35), strengths="Calm under pressure. " * 80,
                        cause_for_concern=True, created_at=NOW, updated_at=NOW))
    db.add(NcoComment(author=author, subject="Uniform", body="Great turnout tonight", cadet_id=2,
                      cadet_name="Ben Brook", comment_date=NOW - timedelta(days=3), created_at=NOW))
    db.add(NcoComment(author=author, subject="Old", body="Long ago", comment_date=NOW - timedelta(days=200),
                      created_at=NOW))
    db.add_all([
        SessionPlan(session_name="Drill basics", session_ic="Cpl Ashworth", status="submitted", author=author,
                    session_date=NOW + timedelta(days=7), created_at=NOW, updated_at=NOW),
        SessionPlan(session_name="Map reading", status="approved", author=author, reviewed_by="oc@317atc.co.uk",
                    created_at=NOW, updated_at=NOW),
    ])
    db.add(CadetLeavingProcess(cadet_id=2, sent_at=NOW - timedelta(days=3), sent_to="mum@example.com"))
    db.add_all(StatsSnapshot(captured_at=NOW - timedelta(weeks=w), data={
        "total_cadets": 60 - w, "by_flight": {"A": 30 - w}, "by_rank": {}, "by_classification": {},
        "badges": {"huge": "x" * 100}}) for w in range(80))
    db.add(Staff(cin=900, first_name="Priya", last_name="Shah", rank="Flt Lt", email="p.shah@example.com",
                 phone_number="07700900999", address="1 Secret Road"))
    db.add_all([StaffAttendance(staff_id=900, date=NOW - timedelta(weeks=w), status="Present") for w in range(3)])
    db.commit()
    return db


# ─── RAFAC documents ─────────────────────────────────────────────────────────

def test_rafac_docs_answers_with_the_documents_it_quotes(db, monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json)
        return httpx.Response(200, json={"answer": "Yes, with Wing approval [1].", "found": True, "sources": [
            {"n": 1, "filename": "ACP 20.pdf", "location": "page 4", "text": "long chunk " * 500}]})

    monkeypatch.setattr(tools.httpx, "post", fake_post)
    out = call(db, "rafac_docs", question="  Can cadets sleep at the centre? ")
    assert sent["url"].endswith("/ask") and sent["json"] == {"question": "Can cadets sleep at the centre?"}
    assert out == {"found": True, "answer": "Yes, with Wing approval [1].",
                   "sources": [{"n": 1, "document": "ACP 20.pdf", "where": "page 4"}]}


@pytest.mark.parametrize("failure, expected", [
    (httpx.ConnectError("refused"), "unreachable"),
    (httpx.ReadTimeout("slow"), "unreachable"),
    (httpx.Response(502, text="bad gateway"), "failed to answer"),
    (httpx.Response(200, text="<html>"), "unreadable"),
])
def test_rafac_docs_failing_is_an_error_not_a_guess(db, monkeypatch, failure, expected):
    def fake_post(*_a, **_kw):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(tools.httpx, "post", fake_post)
    assert expected in call(db, "rafac_docs", question="Ratios?")["error"]


# ─── Events, theory, badges ──────────────────────────────────────────────────

def test_cadet_events_by_cadet_by_event_and_overall(records):
    assert call(records, "cadet_events", cin=1)["events"] == ["Summer Camp", "Summer Camp – Range day"]
    [camp] = call(records, "cadet_events", event="camp")["events"][:1]
    assert [c["name"] for c in camp["cadets"]] == ["Amy Ashworth", "Ben Brook"]
    overall = call(records, "cadet_events")["events"]
    assert overall[0] == {"event": "Summer Camp", "category": None, "cadets": 2}
    assert call(records, "cadet_events", event="nothing")["events"] == []


def test_theory_progress_summary_lesson_and_cadet(records):
    airmanship = next(r for r in call(records, "theory_progress")["lessons"] if r["lesson"] == "Airmanship")
    assert (airmanship["finished"], airmanship["part_done"]) == (1, 1)
    detail = call(records, "theory_progress", lesson="airman")["lessons"][0]
    assert [c["cin"] for c in detail["finished_cadets"]] == [1]
    assert detail["part_done_cadets"][0]["parts_had"] == [1, 2]
    ben = call(records, "theory_progress", cin=2)
    assert ben["lessons"] == [{"lesson": "Airmanship", "group": "Leading", "parts": 3, "done": False,
                               "parts_had": [1, 2]}]
    assert "error" in call(records, "theory_progress", cin=99)


def test_badge_orders_lists_only_badges_still_to_hand_over(records):
    [badge] = call(records, "badge_orders")["badges"]
    assert badge["badge"] == "Radio – Blue" and badge["ready_to_collect"] is True
    assert badge["cadet"]["name"] == "Ben Brook"


# ─── NCO team ────────────────────────────────────────────────────────────────

def test_nco_appraisals_shows_overdue_and_never_appraised(records):
    out = call(records, "nco_appraisals")
    by_cin = {r["cin"]: r for r in out["ncos"]}
    assert set(by_cin) == {1, 3}  # the NCO team only
    assert by_cin[1]["overdue"] is True and by_cin[1]["cause_for_concern"] is True
    assert by_cin[3]["last_appraised"] is None and out["never_appraised"] == 1


def test_one_ncos_appraisal_in_full_is_clipped(records):
    [a] = call(records, "nco_appraisals", cin=1)["appraisals"]
    assert a["strengths"].startswith("Calm under pressure.") and len(a["strengths"]) == 800


def test_nco_comments_recent_by_default_and_by_cadet(records):
    recent = call(records, "nco_comments")["comments"]
    assert [c["subject"] for c in recent] == ["Uniform"]
    assert recent[0]["by"] == "Sam Staff" and recent[0]["about"] == "Ben Brook"
    assert len(call(records, "nco_comments", days=365)["comments"]) == 2
    assert call(records, "nco_comments", cin=1)["comments"] == []


def test_session_plans_all_or_by_status(records):
    assert len(call(records, "session_plans")["plans"]) == 2
    [plan] = call(records, "session_plans", status="submitted")["plans"]
    assert plan["session"] == "Drill basics" and plan["by"] == "Sam Staff"


# ─── Leaving, history, staff ─────────────────────────────────────────────────

def test_leaving_shows_status_and_days_left_but_not_parent_emails(records):
    out = call(records, "leaving")
    [row] = out["cadets"]
    assert (row["cin"], row["status"], row["days_left"]) == (2, "waiting", 11)
    assert "mum@example.com" not in json.dumps(out)


def test_stats_history_is_spread_to_sixty_points_without_badge_detail(records):
    points = call(records, "stats_history", months=24)["points"]
    assert 60 <= len(points) <= 61
    assert points[-1]["total_cadets"] == 60 and "badges" not in points[0]
    assert points == sorted(points, key=lambda p: p["date"])


def test_staff_list_gives_names_and_attendance_but_no_contact_details(records):
    out = call(records, "staff_list")
    assert out["staff"] == [{"cin": 900, "name": "Priya Shah", "rank": "Flt Lt", "present": 3,
                             "authorised": 0, "absent": 0}]
    text = json.dumps(out)
    for secret in ("p.shah@example.com", "07700900999", "Secret Road"):
        assert secret not in text


def test_the_new_tools_work_on_an_empty_database(db):
    for name in ("cadet_events", "theory_progress", "badge_orders", "nco_appraisals", "nco_comments",
                 "session_plans", "leaving", "stats_history", "staff_list"):
        assert "error" not in call(db, name), name


# ─── Programme ───────────────────────────────────────────────────────────────

@pytest.fixture
def programme_month(db, monkeypatch):
    """A and B at drill on a past night, B's half split on a later one; one A
    cadet away from drill (authorised), one B cadet with no register."""
    import core.programme as pg

    def down(*_a, **_kw):
        raise RuntimeError("down")
    monkeypatch.setattr(pg, "generate", down)
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Ash", flight="A"),
                Cadet(cin=2, first_name="Al", last_name="Ash", flight="A"),
                Cadet(cin=3, first_name="Ben", last_name="Brook", flight="B")])
    past = (NOW - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    later = past + timedelta(days=2)
    pg.import_month(db, past.month, past.year, nights=[
        {"date": past, "uniform": "No.3 SD", "dnco": "", "sessions": [
            {"period": 1, "flights": ["A", "B"], "text": "Drill\nSgt Smith", "split": False}]},
    ])
    if later.month != past.month:
        pg.import_month(db, later.month, later.year, nights=[])
    db.add_all([CadetAttendance(cadet_id=1, date=past, status="Present"),
                CadetAttendance(cadet_id=2, date=past, status="Authorised Absence")])
    db.commit()
    return db


def test_programme_lists_nights_with_turnout_by_kind(programme_month):
    out = call(programme_month, "programme")
    [n] = out["nights"]
    assert n["sessions"] == [{"period": 1, "flights": "A, B", "activity": "Drill", "category": "drill"}]
    assert out["turnout_by_kind"][0]["key"] == "drill"


def test_programme_says_who_missed_an_activity(programme_month):
    [n] = call(programme_month, "programme", activity="drill")["nights"]
    assert n["flights"] == ["A", "B"] and n["there"] == 1 and n["no_register"] == 1
    assert [(m["name"], m["absence"]) for m in n["missed"]] == [("Al Ash", "authorised")]


def test_programme_for_one_cadet(programme_month):
    out = call(programme_month, "programme", cin=2)
    assert out["nights"][0]["state"] == "authorised" and out["flight"] == "A"
    assert "error" in call(programme_month, "programme", cin=99)


def test_programme_with_nothing_imported(db):
    assert call(db, "programme")["nights"] == []
    assert call(db, "programme", activity="drill")["nights"] == []
