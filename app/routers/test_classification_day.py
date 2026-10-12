"""Classification day planner over HTTP: who may see it, how theory marks and
Volunteer Portal exam results combine, and the attendance flags. The rules
themselves are pinned in core/test_classification_plan.py."""

from datetime import datetime, timedelta

import pytest

from database.models import Cadet, CadetAbsence, CadetAttendance, CadetPortalData, CadetTheoryProgress

WED = datetime(2026, 10, 14)
NAV, POF, JEP = "acp_32_2", "acp_33_2", "jet_engine_propulsion"


def _exams(*passed, in_progress=()):
    results = [{"courseName": f"{name} Exam", "status": 2, "updatedDate": "2026-09-01T00:00:00"}
               for name in passed]
    results += [{"courseName": f"{name} Exam", "status": 1, "updatedDate": "2026-09-01T00:00:00"}
                for name in in_progress]
    return {"results": results}


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Ready", last_name="Exam", classification="First Class Cadet"),
        Cadet(cin=2, first_name="Passed", last_name="Nav", classification="First Class Cadet"),
        Cadet(cin=3, first_name="Leading", last_name="Lad", classification="Leading Cadet"),
        Cadet(cin=4, first_name="New", last_name="Junior", classification=None),
        Cadet(cin=5, first_name="Wed", last_name="Skipper", classification="Leading Cadet"),
    ])
    # 1: taught Basic Nav by staff, no exam yet.
    db.add(CadetTheoryProgress(cadet_id=1, lesson_key=NAV, completed_at=WED, recorded_by="staff@x"))
    # 2: taught Basic Nav and passed it — the sync kept the staff row, so the
    #    pass is only in the raw exam results. Principles of Flight only started.
    db.add(CadetTheoryProgress(cadet_id=2, lesson_key=NAV, completed_at=WED, recorded_by="staff@x"))
    db.add(CadetPortalData(cadet_id=2, dataset="exams", synced_at=WED,
                           data=_exams("Basic Navigation", in_progress=["Principles of Flight"])))
    # 3: the sync recorded JEP as passed.
    db.add(CadetTheoryProgress(cadet_id=3, lesson_key=JEP, completed_at=WED,
                               recorded_by="Volunteer Portal"))
    # 5: in every Friday, never on a Wednesday; absence booked tonight.
    for w in range(1, 9):
        db.add(CadetAttendance(cadet_id=5, date=WED - timedelta(weeks=w), register_type="Parade Night",
                               status="Absent"))
        db.add(CadetAttendance(cadet_id=5, date=WED - timedelta(weeks=w) + timedelta(days=2),
                               register_type="Parade Night", status="Present Correctly Dressed"))
        # Other registers don't count towards parade attendance.
        db.add(CadetAttendance(cadet_id=5, date=WED - timedelta(weeks=w, days=1), register_type="Camp",
                               status="Present Correctly Dressed"))
    db.add(CadetAbsence(cadet_id=5, date_from=WED - timedelta(days=1), date_to=WED + timedelta(days=1),
                        reason="Holiday", scraped_at=WED))
    db.commit()


def _plan(api, persona="staff", **params):
    return api.get("/classification-day", params={"date": "2026-10-14", **params},
                   headers=api.as_(persona))


def _by_cin(res):
    assert res.status_code == 200, res.text
    return {c["cin"]: c for c in res.json()["cadets"]}


# ── access ────────────────────────────────────────────────────────────────────

def test_no_token_is_401(api):
    assert api.get("/classification-day", params={"date": "2026-10-14"}).status_code == 401
    assert api.get("/classification-day/lessons").status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_non_staff_are_turned_away(api, persona):
    assert _plan(api, persona).status_code == 403
    assert api.get("/classification-day/lessons", headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_staff_can_plan(api, persona):
    assert _plan(api, persona).status_code == 200


# ── input ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("params", [{}, {"date": "14/10/2026"}, {"date": "2026-02-30"}])
def test_a_missing_or_bad_date_is_422(api, params):
    assert api.get("/classification-day", params=params, headers=api.as_("staff")).status_code == 422


def test_an_unknown_lesson_is_400(api):
    res = _plan(api, offered=["basket_weaving"])
    assert res.status_code == 400
    assert "basket_weaving" in res.json()["detail"]


def test_badge_theory_cannot_be_offered(api):
    assert _plan(api, offered=["blue_radio"]).status_code == 400


def test_an_empty_squadron_plans_nothing(api):
    assert _plan(api).json() == {"date": "2026-10-14", "weekday": "Wednesday", "cadets": []}


def test_lessons_lists_the_classification_subjects_only(api):
    lessons = api.get("/classification-day/lessons", headers=api.as_("staff")).json()
    keys = [s["key"] for s in lessons]
    assert keys[:3] == [NAV, POF, "acp_34_2"]
    assert "blue_radio" not in keys and JEP in keys


# ── planning ──────────────────────────────────────────────────────────────────

def test_assignments_combine_theory_marks_and_portal_exams(api, squadron):
    cadets = _by_cin(_plan(api))

    assert (cadets[1]["kind"], cadets[1]["lesson_key"], cadets[1]["lesson_name"]) == (
        "exam", NAV, "Basic Navigation using Map and Compass")
    # The raw portal pass outranks the staff mark; an exam in progress isn't a pass.
    assert (cadets[2]["kind"], cadets[2]["lesson_key"]) == ("lesson", POF)
    # Portal-recorded JEP is passed, not "taught": no exam, next subject instead.
    assert (cadets[3]["kind"], cadets[3]["ready_exams"]) == ("lesson", [])
    assert cadets[3]["lesson_key"] != JEP
    assert (cadets[4]["kind"], cadets[4]["lesson_name"]) == ("first_class", None)


def test_offered_lessons_narrow_assignments_but_never_exams(api, squadron):
    cadets = _by_cin(_plan(api, offered=[JEP]))
    assert cadets[1]["kind"] == "exam"
    assert (cadets[2]["kind"], cadets[2]["lesson_key"]) == ("not_offered", POF)
    assert (cadets[5]["kind"], cadets[5]["lesson_key"]) == ("lesson", JEP)
    # Already passed JEP, and nothing else is running.
    assert cadets[3]["kind"] == "not_offered"


def test_a_cadet_with_no_portal_exams_dataset_still_plans(api, db, squadron):
    # A failed portal call stores nothing; a dataset with no results is harmless.
    db.add(CadetPortalData(cadet_id=1, dataset="exams", synced_at=WED, data={"detail": "error"}))
    db.commit()
    assert _by_cin(_plan(api))[1]["kind"] == "exam"


# ── attendance ────────────────────────────────────────────────────────────────

def test_the_wednesday_skipper_is_flagged_on_a_wednesday_but_still_planned(api, squadron):
    skipper = _by_cin(_plan(api))[5]
    assert skipper["kind"] == "lesson"
    codes = [f["code"] for f in skipper["attendance"]["flags"]]
    assert codes == ["absence_booked", "weekday"]
    # Camp nights are left out: 8 Wednesdays + 8 Fridays.
    assert (skipper["attendance"]["nights"], skipper["attendance"]["weekday_nights"]) == (16, 8)
    assert skipper["attendance"]["likelihood"] == 0.0


def test_the_wednesday_skipper_is_not_flagged_on_a_friday(api, squadron):
    res = api.get("/classification-day", params={"date": "2026-10-16"}, headers=api.as_("staff"))
    skipper = _by_cin(res)[5]
    assert res.json()["weekday"] == "Friday"
    assert skipper["attendance"]["flags"] == []
    assert skipper["attendance"]["likelihood"] == 1.0


def test_cadets_with_no_register_are_flagged_as_no_history(api, squadron):
    assert [f["code"] for f in _by_cin(_plan(api))[1]["attendance"]["flags"]] == ["no_history"]
