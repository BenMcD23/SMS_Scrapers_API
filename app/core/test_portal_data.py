"""Volunteer Portal parsers. Samples for exams, learning and unit history are
trimmed from a real sync; the rest follow the field names the RAFAC Dashy
extension reads. Every parser must shrug off shapes it doesn't expect."""

from datetime import date, datetime

import pytest

from core import portal_data as pd

REAL_EXAMS = {
    "enrolments": [
        {"courseName": "Airmanship", "cadetClassification": "Leading", "enrolled": False,
         "dateEnrolled": "0001-01-01T00:00:00"},
        {"courseName": "Basic Navigation using Map and Compass", "cadetClassification": "Leading",
         "enrolled": True, "dateEnrolled": "2026-09-30T20:23:48.2184846"},
    ],
    "results": [
        {"courseName": "Navigation on Land using Map & Compass Exam", "cadetClassification": "Leading",
         "status": 2, "updatedDate": "2026-09-30T19:36:58"},
        {"courseName": "Principles of Flight", "cadetClassification": "Leading", "status": 1,
         "updatedDate": "2026-10-01T10:00:00"},
    ],
}


@pytest.mark.parametrize("parser", list(pd.PARSERS.values()))
@pytest.mark.parametrize("raw", [None, "", 3, [], {}, [None, 1, "x"], {"whTs": "nope"}])
def test_unexpected_shapes_never_raise(parser, raw):
    parser(raw)


def test_exams_keep_only_real_enrolments_and_name_the_result_status():
    out = pd.exams(REAL_EXAMS)
    assert out["enrolments"] == [{"course": "Basic Navigation using Map and Compass",
                                  "classification": "Leading", "enrolled_on": "2026-09-30"}]
    assert [r["status"] for r in out["results"]] == ["completed", "in_progress"]


def test_dotnet_min_dates_read_as_no_date():
    assert pd.classification({"firstClassPart3Passed": "0001-01-01T00:00:00"})[0]["date"] is None


def test_classification_dates_in_ladder_order():
    out = pd.classification({"leadingCadetPassed": "2025-07-16T00:00:00", "firstClassPart3Passed": "2024-08-30T00:00:00"})
    assert [(r["name"], r["date"]) for r in out] == [
        ("First Class Cadet", "2024-08-30"), ("Leading Cadet", "2025-07-16"),
        ("Senior Cadet", None), ("Master Air Cadet", None),
    ]


def test_learning_and_unit_history_from_real_rows():
    assert pd.learning([{"title": "Airmanship Exam", "satisfied": True, "dateLastAccessed": "2025-05-09T19:39:39",
                         "learningPlatform": "Bader Learn"}]) == [
        {"title": "Airmanship Exam", "complete": True, "date": "2025-05-09", "platform": "Bader Learn"}]
    assert pd.unit_history([{"unitName": "317 (Failsworth & Newton Heath)", "startDate": "2024-03-27T00:00:00",
                             "endDate": None, "isPrimaryUnit": True}]) == [
        {"unit": "317 (Failsworth & Newton Heath)", "start": "2024-03-27", "end": None, "primary": True}]


def test_whts_read_the_portal_envelope():
    raw = {"whTs": [{"weaponCategory": "L98A2", "status": "Pass", "whtDateExpires": "2027-01-01T00:00:00"},
                    {"weaponCategory": "", "status": "Pass"}]}
    assert pd.whts(raw) == [{"weapon": "L98A2", "status": "Pass", "expires": "2027-01-01"}]


@pytest.mark.parametrize("expires,state", [
    ("2026-01-01", "expired"), ("2026-11-01", "expiring"), ("2027-06-01", "current"),
])
def test_wht_state_by_expiry(expires, state):
    out = pd.wht_summary([{"weapon": "L98A2", "status": "Pass", "expires": expires}], date(2026, 10, 8))
    assert out["L98A2"]["state"] == state


def test_a_retest_counts_over_an_older_expired_wht():
    recs = [{"weapon": "L98A2", "status": "Pass", "expires": "2025-01-01"},
            {"weapon": "L98A2", "status": "Pass", "expires": "2027-06-01"}]
    assert pd.wht_summary(recs, date(2026, 10, 8))["L98A2"] == {"state": "current", "expires": "2027-06-01"}


def test_flying_flattens_groups_newest_first():
    raw = {"powered": [{"groupName": "AEF", "entries": [
               {"date": "2026-05-01T00:00:00", "type": "Tutor", "durationMinutes": 25.4, "sortie": "1"},
               {"date": None}]}],
           "gliding": [{"entries": [{"date": "2026-07-01T00:00:00", "type": "Viking", "isPowered": False}]}]}
    out = pd.flying(raw)
    assert [(r["date"], r["activity"], r["minutes"]) for r in out] == [
        ("2026-07-01", "gliding", None), ("2026-05-01", "powered", 25)]


def test_fieldcraft_and_shooting_log():
    fc = pd.fieldcraft({"completions": [{"lessonId": 4, "badgeLevel": "blue", "title": "Camouflage",
                                          "completedDate": "2026-02-01T00:00:00"}]})
    assert fc[0]["level"] == "Blue"
    log = pd.shooting_log([{"weaponCategory": "L98A2", "shootingPractice": "GP", "rangeDate": "2026-03-01"},
                           {"weaponCategory": "L144", "rangeDate": "2026-04-01"}])
    assert [r["weapon"] for r in log] == ["L144", "L98A2"]


@pytest.mark.parametrize("course,key", [
    ("Navigation on Land using Map & Compass Exam", "acp_32_2"),
    ("Basic Navigation using Map and Compass", "acp_32_2"),
    ("Air Navigation", "acp_32_3"),
    ("Pilot Navigation", "acp_32_4"),
    ("Airmanship", "acp_34_2"),
    ("Operational Flying", "acp_34_4"),
    ("Principles of Flight", "acp_33_2"),
    ("Advanced Radio and Radar", "acp_35_3"),
    ("Safeguarding", None),
])
def test_exam_names_map_to_theory_lessons(course, key):
    assert pd.exam_lesson_key(course) == key


def test_only_passed_exams_count_as_theory_done():
    assert pd.completed_theory(REAL_EXAMS) == {"acp_32_2": datetime(2026, 9, 30)}
