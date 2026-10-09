"""Volunteer Portal parsers, against samples trimmed from the first real prod
sync. Every parser must shrug off shapes it doesn't expect."""

from datetime import date, datetime

import pytest

from core import portal_data as pd
from core.theory_lessons import THEORY_LESSON_BY_KEY

REAL_FLYING = {
    "createUrl": "https://volunteers.bader.mod.uk/api/person/x/aviation",
    "gliding": [{"groupName": "VGS", "entries": [
        {"date": "2026-09-27T00:00:00", "duty": "GIF", "type": "Viking", "sortie": "Gliding Induction Flight",
         "isPowered": False, "flyingUnit": "645 VGS (RAF Topcliffe)", "aefCategory": "VGS", "durationMinutes": 11},
    ]}],
    "powered": [
        {"groupName": "AEF", "entries": [
            {"date": "2025-08-06T00:00:00", "duty": "Blue ATP", "type": "Tutor", "sortie": "Sortie 1 - AEF",
             "isPowered": True, "flyingUnit": "11 AEF (RAF Leeming)", "aefCategory": "AEF", "durationMinutes": 25},
        ]},
        {"groupName": "PTT", "entries": [
            {"date": "2023-04-13T00:00:00", "duty": "Blue ATP", "type": "PTT", "sortie": None,
             "isPowered": True, "flyingUnit": "AEF", "aefCategory": "PTT", "durationMinutes": 0},
        ]},
    ],
}

REAL_EXAMS = {
    "enrolments": [{"courseName": "Rocketry", "enrolled": True, "dateEnrolled": "2026-09-30T20:23:48.2184846"}],
    "results": [
        {"courseName": "Navigation on Land using Map & Compass Exam", "status": 2, "updatedDate": "2026-09-30T19:36:58"},
        {"courseName": "Rocketry Exam", "status": 2, "updatedDate": "2025-01-10T10:00:00"},
        {"courseName": "Principles of Flight Exam", "status": 1, "updatedDate": "2026-10-01T10:00:00"},
        {"courseName": "Safeguarding", "status": 2, "updatedDate": "2026-10-01T10:00:00"},
    ],
}


@pytest.mark.parametrize("parser", [pd.flights, pd.classification_dates, pd.joined_on, pd.completed_theory])
@pytest.mark.parametrize("raw", [None, "", 3, [], {}, [None, 1, "x"], {"powered": "nope", "results": "x"},
                                 {"powered": [{"entries": [None, {"date": "not a date"}]}]}])
def test_unexpected_shapes_never_raise(parser, raw):
    parser(raw)


def test_flights_from_a_real_record_newest_first():
    out = pd.flights(REAL_FLYING)
    assert [(f["date"], f["activity"], f["aircraft"], f["duty"]) for f in out] == [
        (date(2026, 9, 27), "gliding", "Viking", "GIF"),
        (date(2025, 8, 6), "powered", "Tutor", "Blue ATP"),
        (date(2023, 4, 13), "simulator", "PTT", "Blue ATP"),
    ]
    assert out[1]["unit"] == "11 AEF (RAF Leeming)"
    assert out[1]["minutes"] == 25


def test_a_simulator_sessions_zero_minutes_reads_as_unknown():
    assert pd.flights(REAL_FLYING)[2]["minutes"] is None


def test_a_cadet_who_never_flew_has_no_flights():
    assert pd.flights({"powered": [], "gliding": [], "createUrl": "x"}) == []


def test_classification_dates_only_for_stages_passed():
    raw = {"probationerDate": None, "firstClassPart3Passed": "2024-08-30T00:00:00",
           "leadingCadetPassed": "2025-07-16T00:00:00", "seniorCadetPassed": None,
           "masterAirCadetPassed": "0001-01-01T00:00:00"}
    assert pd.classification_dates(raw) == {"First Class Cadet": "2024-08-30", "Leading Cadet": "2025-07-16"}


def test_joined_on_is_the_open_primary_units_start():
    units = [
        {"unitName": "2175 Sqn", "startDate": "2022-01-01T00:00:00", "endDate": "2024-03-01T00:00:00", "isPrimaryUnit": True},
        {"unitName": "317 (Failsworth & Newton Heath)", "startDate": "2024-03-27T00:00:00", "endDate": None, "isPrimaryUnit": True},
        {"unitName": "Band", "startDate": "2025-01-01T00:00:00", "endDate": None, "isPrimaryUnit": False},
    ]
    assert pd.joined_on(units) == date(2024, 3, 27)


def test_joined_on_without_an_open_unit_is_none():
    assert pd.joined_on([{"startDate": "2022-01-01T00:00:00", "endDate": "2024-03-01T00:00:00"}]) is None


@pytest.mark.parametrize("course,key", [
    ("Navigation on Land using Map & Compass Exam", "acp_32_2"),
    ("Basic Navigation using Map and Compass", "acp_32_2"),
    ("Airmanship Exam", "acp_34_2"),
    ("Principles of Flight Exam", "acp_33_2"),
    ("Air Navigation Exam", "acp_32_3"),
    ("Principles of Pilot Navigation Exam", "acp_32_4"),
    ("Air Power", "air_power"),
    ("Aircraft Handling and Flying Techniques", "acp_34_3"),
    ("Airframes Exam", "acp_33_4"),
    ("Jet Engine Propulsion Exam", "jet_engine_propulsion"),
    ("Piston Engine Propulsion Exam", "acp_33_3"),
    ("Military Aircraft", "military_aircraft"),
    ("Radio and Radar Exam", "acp_35_3"),
    ("Rocketry Exam", "rocketry"),
    ("Satellite & Data Communications", "acp_35_4"),
    ("Safeguarding Induction", None),
])
def test_every_real_portal_exam_maps_to_its_theory_lesson(course, key):
    assert pd.exam_lesson_key(course) == key


def test_every_mapped_lesson_exists():
    assert {key for _, key in pd.EXAM_LESSON_KEYWORDS} <= THEORY_LESSON_BY_KEY.keys()


def test_only_passed_exams_count_as_theory_done():
    assert pd.completed_theory(REAL_EXAMS) == {
        "acp_32_2": datetime(2026, 9, 30),
        "rocketry": datetime(2025, 1, 10),
    }
