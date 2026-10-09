"""Volunteer Portal sync import: who may post, what it accepts, and that a bad
or partial sync never costs us data we already had."""

import json
from datetime import datetime

import pytest

import routers.volunteer_portal as vp
from database.models import Cadet, CadetPortalData, ScraperRun

CIN = 123456


@pytest.fixture
def cadet(db):
    db.add(Cadet(cin=CIN, first_name="Zoë", last_name="Ó Briain"))
    db.commit()


def _sync(api, cadets, persona="staff"):
    return api.post("/vp-sync", json={"cadets": cadets}, headers=api.as_(persona))


def _stored(db, dataset):
    db.expire_all()
    row = db.query(CadetPortalData).filter_by(cadet_id=CIN, dataset=dataset).one_or_none()
    return row.data if row else None


# ── access ────────────────────────────────────────────────────────────────────

def test_no_token_is_401(api):
    assert api.post("/vp-sync", json={"cadets": []}).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_non_staff_are_refused_and_nothing_is_saved(api, db, cadet, persona):
    r = _sync(api, [{"cin": CIN, "data": {"whts": [{"x": 1}]}}], persona)
    assert r.status_code == 403
    assert _stored(db, "whts") is None


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_staff_can_sync(api, persona):
    assert _sync(api, [], persona).status_code == 200


# ── what it saves ─────────────────────────────────────────────────────────────

def test_a_sync_saves_each_data_set_and_logs_who_ran_it(api, db, cadet):
    whts = [{"weaponCategory": "L98A2", "dateExpires": "2027-01-01"}]
    r = _sync(api, [{"cin": CIN, "data": {"whts": whts, "classification": {"leadingCadetPassed": None}}}])

    assert r.json() == {"matched": 1, "unmatched": 0, "saved": 2, "failed": 0, "kept": 0, "theory": 0, "flights": 0}
    assert _stored(db, "whts") == whts
    assert _stored(db, "classification") == {"leadingCadetPassed": None}
    run = db.query(ScraperRun).one()
    assert (run.scraper_id, run.ran_by, run.success) == ("vp-sync", "staff@317atc.co.uk", True)


def test_cadets_not_in_our_roster_are_ignored(api, db, cadet):
    r = _sync(api, [{"cin": 999, "data": {"whts": [{"x": 1}]}}])
    assert r.json()["unmatched"] == 1
    assert db.query(CadetPortalData).count() == 0


def test_a_later_sync_replaces_the_stored_copy(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"flying": [{"sortie": 1}]}}])
    _sync(api, [{"cin": CIN, "data": {"flying": [{"sortie": 1}, {"sortie": 2}]}}])
    assert _stored(db, "flying") == [{"sortie": 1}, {"sortie": 2}]
    assert db.query(CadetPortalData).count() == 1


def test_a_failed_portal_call_keeps_the_previous_data(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"learning": [{"m": "Safeguarding"}]}}])
    r = _sync(api, [{"cin": CIN, "data": {"learning": None}}])
    assert r.json()["failed"] == 1
    assert _stored(db, "learning") == [{"m": "Safeguarding"}]


def test_an_empty_answer_never_wipes_existing_data(api, db, cadet):
    # A portal hiccup can come back as [] rather than an error.
    _sync(api, [{"cin": CIN, "data": {"fieldcraft": [{"lessonId": 4}]}}])
    r = _sync(api, [{"cin": CIN, "data": {"fieldcraft": []}}])
    assert r.json()["kept"] == 1
    assert _stored(db, "fieldcraft") == [{"lessonId": 4}]


def test_an_empty_answer_is_stored_when_there_was_nothing_before(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"whts": []}}])
    assert _stored(db, "whts") == []


def test_a_cadet_sent_twice_is_one_row_with_the_last_value(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"whts": [1]}}, {"cin": CIN, "data": {"whts": [2]}}])
    assert db.query(CadetPortalData).count() == 1
    assert _stored(db, "whts") == [2]


def test_non_ascii_survives_the_round_trip(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"unit_history": [{"unit": "317 (Failsworth & Newton Heath) — Sqn"}]}}])
    assert _stored(db, "unit_history") == [{"unit": "317 (Failsworth & Newton Heath) — Sqn"}]


def test_the_scrapers_page_sees_the_last_sync(api, cadet):
    _sync(api, [{"cin": CIN, "data": {"whts": []}}])
    last = api.get("/scraper-last-runs", headers=api.as_("staff")).json()["vp-sync"]
    assert last["ran_by"] == "staff@317atc.co.uk"


# ── rejections ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"cadets": [{"cin": CIN, "data": {"passwords": []}}]},      # unknown data set
    {"cadets": [{"cin": CIN, "data": {}, "extra": 1}]},         # unknown field
    {"cadets": [], "extra": 1},
    {"cadets": [{"cin": 0, "data": {}}]},
    {"cadets": [{"cin": "abc", "data": {}}]},
    {"cadets": "nope"},
    {},
])
def test_malformed_payloads_are_422(api, db, cadet, body):
    assert api.post("/vp-sync", json=body, headers=api.as_("staff")).status_code == 422
    assert db.query(ScraperRun).count() == 0


def test_invalid_json_is_422(api):
    r = api.post("/vp-sync", content=b"{not json", headers={**api.as_("staff"), "Content-Type": "application/json"})
    assert r.status_code == 422


def test_too_many_cadets_is_422(api):
    cadets = [{"cin": i, "data": {}} for i in range(1, vp.MAX_CADETS + 2)]
    assert _sync(api, cadets).status_code == 422


def test_an_oversized_data_set_is_422(api, cadet, monkeypatch):
    monkeypatch.setattr(vp, "MAX_DATASET_BYTES", 50)
    assert _sync(api, [{"cin": CIN, "data": {"learning": ["x" * 100]}}]).status_code == 422


def test_an_oversized_body_is_413(api, monkeypatch):
    monkeypatch.setattr(vp, "MAX_BODY_BYTES", 100)
    assert _sync(api, [{"cin": i, "data": {}} for i in range(1, 20)]).status_code == 413


def test_an_oversized_chunked_body_is_413_without_a_content_length(api, monkeypatch):
    # A client can leave Content-Length off; the cap must hold while reading.
    monkeypatch.setattr(vp, "MAX_BODY_BYTES", 100)
    body = json.dumps({"cadets": [{"cin": i, "data": {}} for i in range(1, 20)]}).encode()
    r = api.post("/vp-sync", content=iter([body[:60], body[60:]]),
                 headers={**api.as_("staff"), "Content-Type": "application/json"})
    assert r.status_code == 413


# ── exam results → theory lessons ─────────────────────────────────────────────

PASSED_NAV = {"enrolments": [], "results": [
    {"courseName": "Navigation on Land using Map & Compass Exam", "status": 2, "updatedDate": "2026-09-30T19:36:58"},
    {"courseName": "Principles of Flight", "status": 1, "updatedDate": "2026-10-01T10:00:00"},
]}


def _theory(db):
    from database.models import CadetTheoryProgress
    db.expire_all()
    return {(r.lesson_key, r.recorded_by) for r in db.query(CadetTheoryProgress).filter_by(cadet_id=CIN)}


def test_a_passed_portal_exam_ticks_its_theory_lesson(api, db, cadet):
    r = _sync(api, [{"cin": CIN, "data": {"exams": PASSED_NAV}}])
    assert r.json()["theory"] == 1
    assert _theory(db) == {("acp_32_2", "Volunteer Portal")}  # in-progress POF isn't ticked


def test_resyncing_doesnt_duplicate_or_override_a_staff_mark(api, db, cadet):
    from database.models import CadetTheoryProgress
    db.add(CadetTheoryProgress(cadet_id=CIN, lesson_key="acp_32_2", completed_at=datetime(2026, 1, 1),
                               recorded_by="staff@317atc.co.uk"))
    db.commit()
    _sync(api, [{"cin": CIN, "data": {"exams": PASSED_NAV}}])
    _sync(api, [{"cin": CIN, "data": {"exams": PASSED_NAV}}, {"cin": CIN, "data": {"exams": PASSED_NAV}}])
    assert _theory(db) == {("acp_32_2", "staff@317atc.co.uk")}


def test_a_lesson_the_portal_no_longer_shows_as_passed_is_unticked(api, db, cadet):
    # e.g. the old mapping ticked Piston Propulsion for a Jet Engine pass.
    from database.models import CadetTheoryProgress
    db.add(CadetTheoryProgress(cadet_id=CIN, lesson_key="acp_33_3", completed_at=datetime(2026, 1, 1),
                               recorded_by="Volunteer Portal"))
    db.commit()
    _sync(api, [{"cin": CIN, "data": {"exams": PASSED_NAV}}])
    assert _theory(db) == {("acp_32_2", "Volunteer Portal")}


def test_exams_with_no_results_leave_theory_alone(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"exams": PASSED_NAV}}])
    _sync(api, [{"cin": CIN, "data": {"exams": {"enrolments": [], "results": []}}}])
    assert _theory(db) == {("acp_32_2", "Volunteer Portal")}


# ── flights, join date and classification dates ───────────────────────────────

FLYING = {"createUrl": "x", "gliding": [], "powered": [
    {"groupName": "AEF", "entries": [
        {"date": "2025-08-06T00:00:00", "duty": "Blue ATP", "type": "Tutor", "sortie": "Sortie 1 - AEF",
         "isPowered": True, "flyingUnit": "11 AEF (RAF Leeming)", "aefCategory": "AEF", "durationMinutes": 25}]},
    {"groupName": "PTT", "entries": [
        {"date": "2023-04-13T00:00:00", "duty": "Blue ATP", "type": "PTT", "isPowered": True,
         "aefCategory": "PTT", "durationMinutes": 0}]},
]}
NEVER_FLOWN = {"createUrl": "x", "gliding": [], "powered": []}


def _flights(db):
    from database.models import CadetFlight
    db.expire_all()
    return [(f.date.isoformat(), f.activity, f.aircraft) for f in db.query(CadetFlight).filter_by(cadet_id=CIN).order_by(CadetFlight.date)]


def test_the_flying_record_becomes_the_cadets_flights(api, db, cadet):
    r = _sync(api, [{"cin": CIN, "data": {"flying": FLYING}}])
    assert r.json()["flights"] == 2
    assert _flights(db) == [("2023-04-13", "simulator", "PTT"), ("2025-08-06", "powered", "Tutor")]


def test_a_resync_replaces_flights_rather_than_adding_duplicates(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"flying": FLYING}}])
    _sync(api, [{"cin": CIN, "data": {"flying": FLYING}}])
    assert len(_flights(db)) == 2


def test_an_empty_flying_record_never_wipes_flights(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {"flying": FLYING}}])
    _sync(api, [{"cin": CIN, "data": {"flying": NEVER_FLOWN}}])
    _sync(api, [{"cin": CIN, "data": {"flying": None}}])
    assert len(_flights(db)) == 2


def test_join_date_and_classification_dates_land_on_the_cadet(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {
        "unit_history": [{"unitName": "317", "startDate": "2024-03-27T00:00:00", "endDate": None, "isPrimaryUnit": True}],
        "classification": {"firstClassPart3Passed": "2024-08-30T00:00:00", "leadingCadetPassed": None},
    }}])
    db.expire_all()
    zoe = db.get(Cadet, CIN)
    assert zoe.joined_on.isoformat() == "2024-03-27"
    assert zoe.classification_dates == {"First Class Cadet": "2024-08-30"}


def test_a_failed_read_keeps_the_dates_we_had(api, db, cadet):
    _sync(api, [{"cin": CIN, "data": {
        "unit_history": [{"unitName": "317", "startDate": "2024-03-27T00:00:00", "endDate": None, "isPrimaryUnit": True}],
    }}])
    _sync(api, [{"cin": CIN, "data": {"unit_history": None, "classification": None}}])
    db.expire_all()
    assert db.get(Cadet, CIN).joined_on.isoformat() == "2024-03-27"


def test_the_cadet_page_shows_flights_and_dates_with_everything_else(api, cadet):
    _sync(api, [{"cin": CIN, "data": {"flying": FLYING, "classification": {"leadingCadetPassed": "2025-07-16T00:00:00"}}}])
    body = api.get(f"/cadets/{CIN}", headers=api.as_("staff")).json()
    assert [f["aircraft"] for f in body["flights"]] == ["Tutor", "PTT"]  # newest first
    assert body["classification_dates"] == {"Leading Cadet": "2025-07-16"}
    assert [s["done"] for s in body["flying_blue_stages"]] == [False, True, True]
