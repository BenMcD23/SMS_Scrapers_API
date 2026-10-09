"""Training progress on the stats page — Blue Flying, classification exams and
time served, all from the Volunteer Portal data — its drill-down filters, and
the funnel's timing from the portal's exact dates."""

from datetime import date, datetime, timedelta

import pytest

from database.models import Cadet, CadetFlight, CadetQualification, CadetTheoryProgress

TODAY = date.today()
GROUND = "Blue ATP Ground School"


def _cadet(db, cin, classification="First Class Cadet", flight="A", quals=(), flights=(), theory=(), **kw):
    db.add(Cadet(cin=cin, first_name=f"C{cin}", last_name="Ó Test", flight=flight, classification=classification, **kw))
    for q in quals:
        db.add(CadetQualification(cadet_id=cin, qual_type=q, status="true"))
    for activity, aircraft, days_ago in flights:
        db.add(CadetFlight(cadet_id=cin, date=TODAY - timedelta(days=days_ago), activity=activity, aircraft=aircraft))
    for key in theory:
        db.add(CadetTheoryProgress(cadet_id=cin, lesson_key=key, completed_at=datetime(2026, 1, 1)))
    db.commit()


def _progress(api, query="", persona="nco"):
    res = api.get(f"/stats/progress{query}", headers=api.as_(persona))
    assert res.status_code == 200, res.text
    return res.json()


def _drill(api, query):
    res = api.get(f"/stats/cadets?{query}", headers=api.as_("nco"))
    assert res.status_code == 200, res.text
    return [c["cin"] for c in res.json()["cadets"]]


# ── access ────────────────────────────────────────────────────────────────────

def test_progress_needs_a_token(api):
    assert api.get("/stats/progress").status_code == 401


def test_cadets_cant_see_progress(api):
    assert api.get("/stats/progress", headers=api.as_("cadet")).status_code == 403


@pytest.mark.parametrize("persona", ["nco", "snco", "staff"])
def test_ncos_and_up_can_see_progress(api, persona):
    _progress(api, persona=persona)


# ── Blue Flying ───────────────────────────────────────────────────────────────

@pytest.fixture
def flyers(db):
    _cadet(db, 1, quals=[GROUND, "PTT Blue"], flights=[("powered", "Tutor", 30)])          # done via stages
    _cadet(db, 2, quals=[GROUND], flights=[("gliding", "Viking", 30)])                     # needs PTT
    _cadet(db, 3, quals=[GROUND], flights=[("simulator", "PTT", 30)])                      # needs a flight
    _cadet(db, 4, quals=[GROUND])                                                          # needs both
    _cadet(db, 5, classification=None)                                                     # not started
    _cadet(db, 6, quals=["RAFAC Aviation Training Package Blue Training Badge"])          # Bader says held
    _cadet(db, 7, quals=[GROUND], flights=[("powered", "Tutor", 500)], flight="B")         # needs PTT, flew long ago


def test_blue_flying_pipeline(api, flyers):
    assert _progress(api)["blue_flying"] == {
        "done": 2, "needs_ptt": 2, "needs_flight": 1, "needs_ptt_and_flight": 1, "not_started": 1,
    }


def test_flown_in_the_last_year_ignores_the_simulator_and_old_flights(api, flyers):
    # 1 (Tutor) and 2 (Viking); 3 only did PTT, 7 flew 500 days ago.
    assert _progress(api)["flown_last_year"] == 2


@pytest.mark.parametrize("state,cins", [
    ("done", [1, 6]), ("needs_ptt", [2, 7]), ("needs_flight", [3]),
    ("needs_ptt_and_flight", [4]), ("not_started", [5]),
])
def test_each_blue_flying_count_drills_to_its_cadets(api, flyers, state, cins):
    assert sorted(_drill(api, f"blue_flying={state}")) == cins


def test_the_flight_filter_applies(api, flyers):
    body = _progress(api, "?flight=B")
    assert body["total"] == 1
    assert body["blue_flying"]["needs_ptt"] == 1
    assert _drill(api, "blue_flying=needs_ptt&flight=B") == [7]


def test_excluding_juniors(api, flyers):
    assert _progress(api, "?exclude_juniors=true")["blue_flying"]["not_started"] == 0


# ── classification exams ──────────────────────────────────────────────────────

@pytest.fixture
def examinees(db):
    _cadet(db, 1, classification="First Class Cadet", theory=["acp_34_2"])
    _cadet(db, 2, classification="First Class Cadet")
    _cadet(db, 3, classification="Leading Cadet", theory=["acp_34_2", "rocketry"])
    _cadet(db, 4, classification="Senior Cadet")
    _cadet(db, 5, classification="Master Air Cadet", theory=["rocketry"])  # done with exams


def _exam(body, key):
    return next(e for e in body["exams"] if e["key"] == key)


def test_exams_count_only_the_cadets_who_still_need_them(api, examinees):
    body = _progress(api)
    # Airmanship (Leading): the two First Class cadets need it, one has passed.
    assert _exam(body, "acp_34_2") == {**_exam(body, "acp_34_2"), "cadets": 2, "passed": 1, "category": "Leading"}
    # Rocketry (Senior/Master): the Leading and Senior cadets; the MAC isn't counted.
    assert _exam(body, "rocketry") == {**_exam(body, "rocketry"), "cadets": 2, "passed": 1}


def test_every_exam_subject_is_listed_even_with_no_cadets(api):
    keys = [e["key"] for e in _progress(api)["exams"]]
    assert "rocketry" in keys and "acp_32_2" in keys
    assert "blue_radio" not in keys  # badge theory isn't a classification exam


@pytest.mark.parametrize("passed,cins", [("false", [4]), ("true", [3])])
def test_an_exam_drills_to_who_has_and_hasnt_passed(api, examinees, passed, cins):
    assert _drill(api, f"exam=rocketry&exam_passed={passed}") == cins


# ── time at 317 ───────────────────────────────────────────────────────────────

def test_time_served_bands(api, db):
    _cadet(db, 1, joined_on=TODAY - timedelta(days=30))
    _cadet(db, 2, joined_on=TODAY - timedelta(days=300))
    _cadet(db, 3, joined_on=TODAY - timedelta(days=500))
    _cadet(db, 4, joined_on=TODAY - timedelta(days=1500))
    _cadet(db, 5)
    assert _progress(api)["service"] == {
        "Under 6 months": 1, "6–12 months": 1, "1–2 years": 1, "2–3 years": 0, "3+ years": 1, "Not synced": 1,
    }
    assert _drill(api, "service=1–2 years") == [3]


def test_an_empty_squadron(api):
    body = _progress(api)
    assert body["total"] == 0 and body["flown_last_year"] == 0
    assert set(body["blue_flying"].values()) == {0}


# ── drill-down rejections ─────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "blue_flying=nope",
    "exam=rocketry",                      # needs exam_passed
    "exam_passed=true",                   # needs exam
    "exam=blue_radio&exam_passed=true",   # not a classification exam
    "service=forever",
    f"blue_flying=done&on={(TODAY - timedelta(days=7)).isoformat()}",  # live only
])
def test_bad_progress_drills_are_400(api, query):
    assert api.get(f"/stats/cadets?{query}", headers=api.as_("nco")).status_code == 400


# ── funnel timing from the portal's dates ─────────────────────────────────────

def test_the_funnel_times_steps_from_the_portals_dates(api, db):
    _cadet(db, 1, classification="Leading Cadet", joined_on=date(2024, 1, 1),
           classification_dates={"First Class Cadet": "2024-04-10", "Leading Cadet": "2025-01-05"})
    _cadet(db, 2, classification="Leading Cadet", joined_on=date(2024, 3, 1),
           classification_dates={"First Class Cadet": "2024-05-01", "Leading Cadet": "2024-12-01"})
    # Arrived from another squadron already First Class: not timed for First Class.
    _cadet(db, 3, classification="First Class Cadet", joined_on=date(2025, 6, 1),
           classification_dates={"First Class Cadet": "2024-02-01"})
    steps = {s["name"]: s for s in api.get("/stats/funnel", headers=api.as_("nco")).json()["steps"]}
    first, leading = steps["First Class Cadet"], steps["Leading Cadet"]
    assert first["timed_cadets"] == 2
    # 2024-03-01→05-01 is 61 days, 2024-01-01→04-10 is 100; with two, the upper middle.
    assert first["median_days_from_previous"] == 100
    assert leading["timed_cadets"] == 2
    assert leading["median_days_from_previous"] == 270  # 214 and 270 days
