"""The programme record: tidying the doc's cells into activities (by the
model and by the rules), importing a month, and turnout set against it.

The cell texts are the doc's own wording from real months; the people in them
are made up. No network: the model is a fake, and the doc arrives as already
parsed nights (the parser has its own tests).
"""

import json
from datetime import date, datetime

import pytest

import core.programme as pg
from core.programme import Activity, activities_by_rules, category_for
from database.models import Cadet, CadetAttendance, ProgrammeNight, ProgrammeSession

# ─── Rules ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cell, expected", [
    ("Map Reading 2\nSgt Smith", [("Map Reading 2", "fieldcraft", "Sgt Smith")]),
    ("LO2 History of the RAF\nCWO Jones", [("History of the RAF", "lessons", "CWO Jones")]),
    ("Volleyball / Band\nSgt Smith / FS Brown",
     [("Volleyball", "sport", "Sgt Smith"), ("Band", "music", "FS Brown")]),
    ("Drill / Uniform Measurement\nFS Gray / CI Black",
     [("Drill", "drill", "FS Gray"), ("Uniform Measurement", "admin", "CI Black")]),
    # The second line is still the activity's name until a person starts.
    ("Inter Flight First Aid Comp\n(Op Mass Panic)\nFS Brown",
     [("Inter Flight First Aid Comp (Op Mass Panic)", "first_aid", "FS Brown")]),
    ("Halloween Extravaganza\nSNCOs", [("Halloween Extravaganza", "social", "SNCOs")]),
    ("Flight Time\nFlight Commander", [("Flight Time", "admin", "Flight Commander")]),
    ("Classifications (Leading/Senior/Master)\nFg Off Grey / MOI Cadets",
     [("Classifications (Leading/Senior/Master)", "classifications", "Fg Off Grey / MOI Cadets")]),
    ("Cadets Own Session\nCdt Green", [("Cadets Own Session", "leadership", "Cdt Green")]),
    ("Inter Flight Indoor STEM Exercise\nFg Off Grey & CI Black",
     [("Inter Flight Indoor STEM Exercise", "stem", "Fg Off Grey & CI Black")]),
    ("Something Unusual", [("Something Unusual", "other", None)]),
])
def test_rules_turn_a_cell_into_activities(cell, expected):
    assert [(a.name, a.category, a.instructor) for a in activities_by_rules(cell)] == expected


def test_a_split_with_too_few_instructors_gives_each_the_whole_line():
    acts = activities_by_rules("Drill / Band / Sport\nSgt Smith / FS Brown")
    assert [a.instructor for a in acts] == ["Sgt Smith / FS Brown"] * 3


def test_an_empty_cell_is_no_activity():
    assert activities_by_rules("  \n ") == []


def test_keywords_only_match_at_the_start_of_a_word():
    # "iet" (IET, expedition training) mustn't catch "Variety Night".
    assert category_for("IET Pt 1") == "fieldcraft"
    assert category_for("Variety Night") == "other"


# ─── The model ───────────────────────────────────────────────────────────────

@pytest.fixture
def model(monkeypatch):
    state = {"reply": None, "calls": []}

    def fake_generate(prompt, system, **_kw):
        state["calls"].append(prompt)
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"], "z-ai/glm-5.3"

    monkeypatch.setattr(pg, "generate", fake_generate)
    return state


def test_the_model_tidies_cells_and_keeps_one_activity_with_a_slash_in_it(model):
    model["reply"] = json.dumps({"cells": [
        {"i": 0, "activities": [{"name": "Classifications", "category": "classifications",
                                 "instructor": "Sgt Smith / Cpl Jones"}]},
        {"i": 1, "activities": [{"name": "Volleyball", "category": "sport", "instructor": "Sgt Smith"},
                                {"name": "Band", "category": "music", "instructor": "FS Brown"}]},
    ]})
    out = pg.activities_by_ai(["Classifications (AIR) / Senior/Master\nSgt Smith / Cpl Jones",
                               "Volleyball / Band\nSgt Smith / FS Brown"])
    assert out[0] == [Activity("Classifications", "classifications", "Sgt Smith / Cpl Jones")]
    assert [a.name for a in out[1]] == ["Volleyball", "Band"]


@pytest.mark.parametrize("reply", [
    "not json",
    json.dumps({"cells": "nope"}),
    json.dumps({"cells": [{"i": 0, "activities": [{"name": "Drill", "category": "marching"}]}]}),  # bad category
    json.dumps({"cells": [{"i": 7, "activities": [{"name": "Drill", "category": "drill"}]}]}),  # no such cell
    json.dumps({"cells": [{"i": 0, "activities": []}]}),
    json.dumps({"cells": [{"activities": [{"name": "Drill", "category": "drill"}]}]}),  # no index
])
def test_anything_the_model_gets_wrong_is_left_for_the_rules(model, reply):
    model["reply"] = reply
    assert pg.activities_by_ai(["Drill\nSgt Smith"]) == {}


def test_every_model_failing_falls_back_to_the_rules(db, model):
    model["reply"] = RuntimeError("every model in the chain failed")
    out = pg.tidy_cells(db, ["Drill\nSgt Smith"])
    assert out["Drill\nSgt Smith"] == [Activity("Drill", "drill", "Sgt Smith")]


def test_tidying_reuses_cells_already_stored_and_asks_only_about_new_ones(db, model):
    night = ProgrammeNight(date=date(2026, 10, 2), imported_at=datetime.now())
    night.sessions = [ProgrammeSession(period=1, flight=f, activity="Rank Structure", category="lessons",
                                       instructor="Sgt Smith", raw="Rank System\nSgt Smith") for f in "AB"]
    db.add(night)
    db.commit()
    model["reply"] = json.dumps({"cells": [{"i": 0, "activities": [
        {"name": "Map Reading 1", "category": "fieldcraft", "instructor": None}]}]})

    out = pg.tidy_cells(db, ["Rank System\nSgt Smith", "Map Reading 1", "Map Reading 1", " "])
    # The stored name wins (a staff-tidied one stays), once per activity not per flight.
    assert out["Rank System\nSgt Smith"] == [Activity("Rank Structure", "lessons", "Sgt Smith")]
    assert out["Map Reading 1"][0].name == "Map Reading 1"
    [prompt] = model["calls"]
    assert "Map Reading 1" in prompt and "Rank System" not in prompt


def test_without_ai_the_model_isnt_asked(db, model):
    pg.tidy_cells(db, ["Drill"], use_ai=False)
    assert model["calls"] == []


# ─── Importing a month ───────────────────────────────────────────────────────

def night(day, *sessions, uniform="No.3 SD", dnco="Cpl Jones"):
    return {"date": datetime(2026, 10, day), "uniform": uniform, "dnco": dnco,
            "sessions": [{"period": p, "flights": f, "text": t, "split": s} for p, f, t, s in sessions]}


OCTOBER = [
    night(7, (1, ["C"], "Intro to Drill\nFS Gray", False), (1, ["A", "B"], "Flight Time\nFlight Commander", False),
          (2, ["C", "A", "B"], "Remembrance Parade Practice\nFS Gray", False)),
    night(9, (1, ["A"], "Volleyball / Band\nSgt Smith / FS Brown", False), (1, ["B"], "Drill\nSgt Smith", False)),
]


def test_import_stores_a_row_per_flight_per_activity(db, model):
    model["reply"] = RuntimeError("down")  # the rules are enough here
    result = pg.import_month(db, 10, 2026, nights=OCTOBER)
    assert result == {"month": "2026-10", "nights": 2, "sessions": 9}
    seventh = db.query(ProgrammeNight).filter_by(date=date(2026, 10, 7)).one()
    assert (seventh.uniform, seventh.duty_nco) == ("No.3 SD", "Cpl Jones")
    assert sorted((s.period, s.flight, s.activity) for s in seventh.sessions) == [
        (1, "A", "Flight Time"), (1, "B", "Flight Time"), (1, "C", "Intro to Drill"),
        (2, "A", "Remembrance Parade Practice"), (2, "B", "Remembrance Parade Practice"),
        (2, "C", "Remembrance Parade Practice")]
    ninth = db.query(ProgrammeNight).filter_by(date=date(2026, 10, 9)).one()
    a_flight = [s for s in ninth.sessions if s.flight == "A"]
    # A Flight was split between volleyball and band; B wasn't.
    assert {s.activity for s in a_flight} == {"Volleyball", "Band"} and all(s.split for s in a_flight)
    assert [s.split for s in ninth.sessions if s.flight == "B"] == [False]


def test_reimporting_a_month_replaces_it_and_leaves_other_months_alone(db, model):
    model["reply"] = RuntimeError("down")
    db.add(ProgrammeNight(date=date(2026, 9, 30), imported_at=datetime.now()))
    db.commit()
    pg.import_month(db, 10, 2026, nights=OCTOBER)
    pg.import_month(db, 10, 2026, nights=OCTOBER[:1])
    assert [n.date.day for n in db.query(ProgrammeNight).order_by(ProgrammeNight.date)] == [30, 7]
    assert db.query(ProgrammeSession).count() == 6  # the 9th's sessions went with it


def test_a_doc_with_no_nights_never_wipes_the_month(db, model):
    model["reply"] = RuntimeError("down")
    pg.import_month(db, 10, 2026, nights=OCTOBER)
    result = pg.import_month(db, 10, 2026, nights=[])
    assert result["skipped"] == "no nights in the doc"
    assert db.query(ProgrammeNight).count() == 2


def test_import_months_carries_on_past_a_month_that_fails(db, monkeypatch):
    def fake_import(db, month, year, use_ai=True):
        if month == 2:
            raise FileNotFoundError("No '02_26' programme doc in the 2026 folder")
        return {"month": f"{year}-{month:02d}", "nights": 1, "sessions": 1}

    monkeypatch.setattr(pg, "import_month", fake_import)
    results = pg.import_months(db, [(1, 2026), (2, 2026), (3, 2026)])
    assert [r.get("skipped") for r in results] == [None, "No '02_26' programme doc in the 2026 folder", None]


def test_months_between_crosses_years():
    assert pg.months_between(date(2025, 11, 1), date(2026, 2, 1)) == [(11, 2025), (12, 2025), (1, 2026), (2, 2026)]
    assert pg.months_between(date(2026, 3, 1), date(2026, 2, 1)) == []


# ─── Turnout against the programme ───────────────────────────────────────────

@pytest.fixture
def squadron(db, model):
    """Two A, one B, one C cadet and an NCO-flight cadet over the October
    nights: everyone at drill on the 9th, A missing flight time on the 7th."""
    model["reply"] = RuntimeError("down")
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="A", flight="A"),
                Cadet(cin=2, first_name="Al", last_name="A", flight="A"),
                Cadet(cin=3, first_name="Ben", last_name="B", flight="B"),
                Cadet(cin=4, first_name="Cal", last_name="C", flight="C"),
                Cadet(cin=5, first_name="Nia", last_name="N", flight="NCO")])
    pg.import_month(db, 10, 2026, nights=OCTOBER)

    def reg(cin, day, status):
        db.add(CadetAttendance(cadet_id=cin, date=datetime(2026, 10, day), status=status))
    for cin, status in [(1, "Absent"), (2, "Authorised Absence"), (3, "Present"), (4, "Present"), (5, "Absent")]:
        reg(cin, 7, status)
    for cin in (1, 2, 3, 4, 5):
        reg(cin, 9, "Present Correctly Dressed")
    db.commit()
    return db


def test_turnout_by_category_activity_and_night(squadron):
    out = pg.programme_stats(squadron, days=60, today=date(2026, 10, 31))
    by_cat = {c["key"]: c for c in out["categories"]}
    # Flight time on the 7th: A and B. A were both away (one authorised), B there.
    assert by_cat["admin"]["rate"] == pytest.approx(33.3) and by_cat["admin"]["authorised_rate"] == pytest.approx(33.3)
    # Drill: C's intro on the 7th (1 of 1), the parade practice on the 7th (2 of 4), B on the 9th (1 of 1).
    assert by_cat["drill"]["nights"] == 2 and by_cat["drill"]["rate"] == pytest.approx(66.7)
    # The split A-flight session on the 9th counts towards nothing.
    assert "sport" not in by_cat and "music" not in by_cat
    # The NCO flight cadet was absent on the 7th but isn't in any figure.
    assert [(n["date"], n["present"], n["total"]) for n in out["nights"]] == [
        ("2026-10-07", 2, 4), ("2026-10-09", 4, 4)]
    nine = out["nights"][1]["sessions"]
    assert {"period": 1, "flights": "B", "activity": "Drill", "category": "drill"} in nine


def test_activities_need_two_nights_to_be_a_pattern(squadron):
    out = pg.programme_stats(squadron, days=60, today=date(2026, 10, 31))
    assert out["activities"] == []  # nothing ran twice
    pg.import_month(squadron, 11, 2026, nights=[{**night(4, (1, ["B"], "Drill\nSgt Smith", False)),
                                                 "date": datetime(2026, 11, 4)}])
    squadron.add(CadetAttendance(cadet_id=3, date=datetime(2026, 11, 4), status="Absent"))
    squadron.commit()
    [drill] = pg.programme_stats(squadron, days=60, today=date(2026, 11, 30))["activities"]
    assert (drill["name"], drill["times"], drill["rate"]) == ("Drill", 2, 50.0)


def test_planned_nights_without_a_register_say_nothing(db, model):
    model["reply"] = RuntimeError("down")
    pg.import_month(db, 10, 2026, nights=OCTOBER)
    out = pg.programme_stats(db, days=60, today=date(2026, 10, 31))
    assert out == {"days": 60, "nights": [], "categories": [], "activities": []}


def test_future_nights_are_left_out(squadron):
    out = pg.programme_stats(squadron, days=60, today=date(2026, 10, 8))
    assert [n["date"] for n in out["nights"]] == ["2026-10-07"]


def test_one_cadets_nights_and_categories(squadron):
    out = pg.cadet_sessions(squadron, 1, days=60, today=date(2026, 10, 31))
    assert out["flight"] == "A"
    assert out["nights"] == [
        {"date": "2026-10-07", "state": "absent",
         "activities": ["Flight Time", "Remembrance Parade Practice"]},
        {"date": "2026-10-09", "state": "present", "activities": ["Band", "Volleyball"]},
    ]
    by_cat = {c["category"]: c for c in out["by_category"]}
    assert by_cat["Drill and inspection"] == {"category": "Drill and inspection", "present": 0, "authorised": 0,
                                              "absent": 1, "rate": 0.0}
    assert "Sport and fitness" not in by_cat  # split night


def test_cadet_sessions_for_an_unknown_cadet(db):
    assert "error" in pg.cadet_sessions(db, 99)


def test_every_rule_category_has_a_label():
    assert set(pg.CATEGORIES) >= {c for c, _ in pg._RULES} | {"other"}


# ─── The weekly import ───────────────────────────────────────────────────────

def test_the_weekly_job_imports_last_this_and_next_month(session_factory, monkeypatch):
    import core.jobs as jobs

    asked = []
    monkeypatch.setattr(jobs.programme, "import_months", lambda db, months: asked.append(months))

    class Today(date):
        @classmethod
        def today(cls):
            return date(2026, 1, 15)

    monkeypatch.setattr(jobs, "date", Today)
    jobs.import_recent_programme()
    assert asked == [[(12, 2025), (1, 2026), (2, 2026)]]


def test_the_weekly_job_logs_a_failure_rather_than_raising(session_factory, monkeypatch, caplog):
    import core.jobs as jobs

    def boom(db, months):
        raise RuntimeError("drive gone")

    monkeypatch.setattr(jobs.programme, "import_months", boom)
    jobs.import_recent_programme()
    assert "weekly programme import failed" in caplog.text


def test_the_weekly_job_is_scheduled_on_sunday_evening(monkeypatch):
    import core.jobs as jobs

    added = []

    class Sched:
        def add_job(self, func, trigger=None, **kw):
            added.append((func.__name__, str(trigger)))

    monkeypatch.setattr(jobs.scrapers, "register_schedule_jobs", lambda: None)
    jobs.register_jobs(Sched())
    [(_, trigger)] = [a for a in added if a[0] == "import_recent_programme"]
    assert "day_of_week='sun'" in trigger and "hour='20'" in trigger
