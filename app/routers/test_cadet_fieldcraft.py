"""Fieldcraft lessons from the Volunteer Portal: the unit-wide grid
(GET /cadets/fieldcraft) and each cadet's own record on GET /cadets/{cin}."""

from datetime import datetime

import pytest

from database.models import Cadet, CadetPortalData

SYNCED = datetime(2026, 11, 4, 20, 10)


def _lesson(lesson_id, level, title, when="2026-05-16T00:00:00"):
    return {"lessonId": lesson_id, "badgeLevel": level, "reference": f"Lesson {lesson_id}", "title": title,
            "completedDate": when, "deliveredBy": "Sgt Ó Briain"}


@pytest.fixture
def unit(db):
    """Three cadets: one through Blue into Bronze, one part-way through Blue,
    and one the sync has never read."""
    db.add_all([
        Cadet(cin=1, first_name="Zoë", last_name="Adams", flight="A"),
        Cadet(cin=2, first_name="Ben", last_name="Brown", flight="B"),
        Cadet(cin=3, first_name="Cal", last_name="Cole"),
    ])
    db.add_all([
        CadetPortalData(cadet_id=1, dataset="fieldcraft", synced_at=SYNCED, data={"completions": [
            _lesson(1, "Blue", "Introduction"), _lesson(2, "Blue", "Packing"),
            _lesson(12, "Bronze", "Moving", when="0001-01-01T00:00:00"),
        ]}),
        CadetPortalData(cadet_id=2, dataset="fieldcraft", synced_at=datetime(2026, 10, 1),
                        data={"completions": [_lesson(1, "Blue", "Introduction", when="2025-01-02T00:00:00")]}),
        # Another data set for cadet 3 doesn't count as a fieldcraft record.
        CadetPortalData(cadet_id=3, dataset="flying", synced_at=SYNCED, data={}),
    ])
    db.commit()


# ── access ────────────────────────────────────────────────────────────────────

def test_no_token_is_401(api):
    assert api.get("/cadets/fieldcraft").status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_see_the_grid(api, persona):
    assert api.get("/cadets/fieldcraft", headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_staff_see_the_grid(api, persona):
    assert api.get("/cadets/fieldcraft", headers=api.as_(persona)).status_code == 200


# ── the grid ──────────────────────────────────────────────────────────────────

def test_the_grid_lists_the_syllabus_and_who_has_done_what(api, unit):
    out = api.get("/cadets/fieldcraft", headers=api.as_("staff")).json()

    assert out["synced_at"] == SYNCED.isoformat()
    assert [(lvl["level"], [ls["title"] for ls in lvl["lessons"]]) for lvl in out["levels"]] == [
        ("Blue", ["Introduction", "Packing"]), ("Bronze", ["Moving"])]
    by_cin = {c["cin"]: c for c in out["cadets"]}
    assert [c["cin"] for c in out["cadets"]] == [1, 2, 3]  # surname order
    assert by_cin[1]["done"] == {"1": "2026-05-16", "2": "2026-05-16", "12": None}  # done, no date
    assert by_cin[2]["done"] == {"1": "2025-01-02"}
    assert (by_cin[1]["first_name"], by_cin[1]["flight"]) == ("Zoë", "A")
    # Never synced is "unknown", not "nothing done".
    assert (by_cin[3]["synced"], by_cin[3]["done"]) == (False, {})
    assert by_cin[2]["synced"] is True


def test_no_fieldcraft_synced_is_an_empty_grid_not_an_error(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B"))
    db.commit()
    out = api.get("/cadets/fieldcraft", headers=api.as_("staff")).json()
    assert (out["synced_at"], out["levels"]) == (None, [])
    assert out["cadets"][0]["synced"] is False


def test_a_record_in_a_shape_we_dont_expect_reads_as_nothing_done(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B"))
    db.add(CadetPortalData(cadet_id=1, dataset="fieldcraft", synced_at=SYNCED, data="not a record"))
    db.commit()
    out = api.get("/cadets/fieldcraft", headers=api.as_("staff")).json()
    assert out["levels"] == []
    assert out["cadets"][0]["done"] == {}


# ── one cadet ─────────────────────────────────────────────────────────────────

def test_a_cadets_page_shows_their_lessons_against_the_syllabus(api, unit):
    out = api.get("/cadets/2", headers=api.as_("staff")).json()["fieldcraft"]
    assert [(lvl["level"], [(ls["title"], ls["done"], ls["completed_on"]) for ls in lvl["lessons"]]) for lvl in out] == [
        ("Blue", [("Introduction", True, "2025-01-02"), ("Packing", False, None)]),
        ("Bronze", [("Moving", False, None)]),
    ]
    assert out[0]["lessons"][0]["delivered_by"] == "Sgt Ó Briain"
    assert out[0]["lessons"][1]["delivered_by"] is None


def test_a_lesson_done_without_a_date_still_shows_done(api, unit):
    bronze = api.get("/cadets/1", headers=api.as_("staff")).json()["fieldcraft"][1]
    assert bronze["lessons"][0] == {"lesson_id": 12, "level": "Bronze", "reference": "Lesson 12", "title": "Moving",
                                    "done": True, "completed_on": None, "delivered_by": "Sgt Ó Briain"}


def test_a_cadet_the_sync_never_read_has_no_fieldcraft_record(api, unit):
    assert api.get("/cadets/3", headers=api.as_("staff")).json()["fieldcraft"] is None
