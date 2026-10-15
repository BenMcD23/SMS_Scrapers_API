"""Theory lessons over HTTP: who may record and read them, how a subject's three
lessons add up to "taught", and the cadet's QR check-in."""

from datetime import datetime

import pytest

from core.theory_progress import QR_CHECK_IN
from database.models import (
    Cadet,
    CadetPortalData,
    CadetQualification,
    CadetTheoryLesson,
    CadetTheoryProgress,
    TheoryLessonCode,
)

STAFF = {"Authorization": "Bearer staff"}
CADET = {"Authorization": "Bearer cadet"}
NAV, POF = "acp_32_2", "acp_33_2"


@pytest.fixture
def roster(db):
    db.add_all([
        Cadet(cin=1, first_name="Cara", last_name="Cadet", email="cadet@317atc.co.uk",
              classification="First Class Cadet", flight="A"),
        Cadet(cin=2, first_name="Bob", last_name="Brown", classification="Leading Cadet", flight="B"),
        Cadet(cin=3, first_name="Ann", last_name="Archer"),
    ])
    db.commit()


def mark(api, cins, lessons, completed=True, headers=STAFF):
    return api.post("/cadets/theory/mark", json={"cadet_cins": cins, "lessons": lessons, "completed": completed},
                    headers=headers)


def parts(db, cin, key):
    return sorted(p for (p,) in db.query(CadetTheoryLesson.part).filter_by(cadet_id=cin, lesson_key=key))


def taught(db, cin, key):
    return db.query(CadetTheoryProgress).filter_by(cadet_id=cin, lesson_key=key).first()


def code_for(api, key, part):
    codes = api.get("/cadets/theory/qr-codes", headers=STAFF).json()
    return next(c["code"] for c in codes if c["key"] == key and c["part"] == part)


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/cadets/theory/lessons", "/cadets/theory/progress", "/cadets/theory/qr-codes"])
def test_reads_are_staff_only(api, roster, path):
    assert api.get(path).status_code == 401
    for persona in ("snco", "nco", "cadet"):
        assert api.get(path, headers=api.as_(persona)).status_code == 403, persona
    for persona in ("staff", "owner", "oc"):
        assert api.get(path, headers=api.as_(persona)).status_code == 200, persona


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_record_lessons(api, roster, db, persona):
    res = mark(api, [1], [{"key": NAV}], headers=api.as_(persona))
    assert res.status_code == 403
    assert db.query(CadetTheoryLesson).count() == 0


def test_recording_needs_a_token(api):
    assert api.post("/cadets/theory/mark", json={}).status_code == 401


# ── catalogue ─────────────────────────────────────────────────────────────────

def test_classification_subjects_have_three_lessons_and_badges_one(api):
    lessons = {lesson["key"]: lesson for lesson in api.get("/cadets/theory/lessons", headers=STAFF).json()}
    assert lessons["blue_radio"] == {"key": "blue_radio", "name": "Blue Radio", "category": "Badges", "parts": 1}
    assert lessons[NAV]["parts"] == 3 and lessons[NAV]["category"] == "Leading"
    assert lessons["rocketry"]["parts"] == 3


# ── recording ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lessons,detail", [
    ([], "Select at least one"),
    ([{"key": "nonsense"}], "Unknown subject"),
    ([{"key": NAV, "part": 4}], "only has 3"),
    ([{"key": "blue_radio", "part": 2}], "only has 1"),
])
def test_bad_lessons_are_refused_not_skipped(api, roster, db, lessons, detail):
    res = mark(api, [1], lessons)
    assert res.status_code == 400 and detail in res.json()["detail"]
    assert db.query(CadetTheoryLesson).count() == 0


def test_lesson_zero_is_a_validation_error(api, roster):
    assert mark(api, [1], [{"key": NAV, "part": 0}]).status_code == 422


def test_no_cadets_or_only_unknown_cadets_is_400(api, roster):
    assert mark(api, [], [{"key": NAV}]).status_code == 400
    res = mark(api, [99], [{"key": NAV}])
    assert res.status_code == 400 and "No matching cadets" in res.json()["detail"]


def test_a_subject_is_taught_only_once_all_three_lessons_are_in(api, roster, db):
    mark(api, [1], [{"key": NAV, "part": 1}, {"key": NAV, "part": 3}])
    assert parts(db, 1, NAV) == [1, 3] and taught(db, 1, NAV) is None

    res = mark(api, [1], [{"key": NAV, "part": 2}]).json()
    assert res == {"status": "success", "action": "marked", "changed": 1}
    assert taught(db, 1, NAV).recorded_by == "staff@317atc.co.uk"


def test_marking_a_whole_subject_records_every_lesson_and_is_idempotent(api, roster, db):
    res = mark(api, [1, 2, 99], [{"key": NAV}, {"key": "blue_radio"}]).json()
    assert res["changed"] == 2 * (3 + 1)
    assert parts(db, 2, NAV) == [1, 2, 3] and taught(db, 2, NAV)
    assert taught(db, 1, "blue_radio")  # one lesson is the whole of badge theory
    assert mark(api, [1, 2], [{"key": NAV}]).json()["changed"] == 0
    assert db.query(CadetTheoryProgress).count() == 4


def test_clearing_one_lesson_untaughts_the_subject_but_keeps_the_rest(api, roster, db):
    mark(api, [1], [{"key": NAV}])
    res = mark(api, [1], [{"key": NAV, "part": 2}], completed=False).json()
    assert res == {"status": "success", "action": "cleared", "changed": 1}
    assert parts(db, 1, NAV) == [1, 3]
    assert taught(db, 1, NAV) is None


def test_clearing_never_removes_a_portal_exam_pass(api, roster, db):
    # The sync's row means the exam was passed — not ours to take back.
    db.add(CadetTheoryProgress(cadet_id=1, lesson_key=NAV, completed_at=datetime(2026, 9, 1),
                               recorded_by="Volunteer Portal"))
    db.commit()
    mark(api, [1], [{"key": NAV}])
    mark(api, [1], [{"key": NAV}], completed=False)
    assert parts(db, 1, NAV) == []
    assert taught(db, 1, NAV).recorded_by == "Volunteer Portal"


def test_clearing_lessons_nobody_had_changes_nothing(api, roster, db):
    db.add(CadetTheoryProgress(cadet_id=1, lesson_key=NAV, completed_at=datetime(2026, 9, 1), recorded_by="x@y"))
    db.commit()
    assert mark(api, [1], [{"key": POF}], completed=False).json()["changed"] == 0
    assert taught(db, 1, NAV)  # a different subject's mark is untouched


# ── progress ──────────────────────────────────────────────────────────────────

def test_progress_shows_lessons_taught_and_passed_per_subject(api, roster, db):
    mark(api, [1], [{"key": NAV, "part": 1}, {"key": NAV, "part": 2}, {"key": POF}])
    db.add(CadetPortalData(cadet_id=1, dataset="exams", synced_at=datetime(2026, 9, 2),
                           data={"results": [{"courseName": "Principles of Flight Exam", "status": 2,
                                              "updatedDate": "2026-09-01T00:00:00"}]}))
    db.add(CadetQualification(cadet_id=3, qual_type="Radio - Basic Operator (Blue)", status="true"))
    db.commit()

    rows = {r["cin"]: r for r in api.get("/cadets/theory/progress", headers=STAFF).json()}
    assert [r["cin"] for r in api.get("/cadets/theory/progress", headers=STAFF).json()] == [3, 2, 1]

    cara = rows[1]["subjects"]
    assert cara[NAV] == {"lessons": [1, 2], "taught": False, "passed": False}
    assert cara[POF] == {"lessons": [1, 2, 3], "taught": True, "passed": True}
    assert "acp_34_2" not in cara  # nothing to show is left out

    # A Leading Cadet has passed every Leading subject, lessons or not.
    assert rows[2]["subjects"][NAV] == {"lessons": [], "taught": False, "passed": True}
    assert rows[3]["subjects"] == {"blue_radio": {"lessons": [], "taught": False, "passed": True}}


def test_progress_with_no_cadets_is_empty(api):
    assert api.get("/cadets/theory/progress", headers=STAFF).json() == []


# ── QR codes ──────────────────────────────────────────────────────────────────

def test_every_lesson_gets_a_code_that_never_changes(api, db):
    first = api.get("/cadets/theory/qr-codes", headers=STAFF).json()
    nav = [c for c in first if c["key"] == NAV]
    assert [c["part"] for c in nav] == [1, 2, 3]
    assert len({c["code"] for c in first}) == len(first)  # every lesson its own
    assert all(len(c["code"]) >= 8 for c in first)
    assert api.get("/cadets/theory/qr-codes", headers=STAFF).json() == first
    assert db.query(TheoryLessonCode).count() == len(first)


def test_a_new_subject_gets_a_code_without_changing_the_old_ones(api, db):
    db.add(TheoryLessonCode(code="keepme12", lesson_key=NAV, part=1, created_at=datetime(2026, 1, 1)))
    db.commit()
    codes = api.get("/cadets/theory/qr-codes", headers=STAFF).json()
    assert code_for(api, NAV, 1) == "keepme12"
    assert len(codes) == db.query(TheoryLessonCode).count()


def test_two_staff_opening_the_codes_at_once_both_get_the_same_codes(db, monkeypatch):
    # The other request commits its codes between our read and our write: ours
    # hit the unique constraint, and we answer with theirs instead of a 500.
    from core import theory_progress

    real_commit, raced = db.commit, []

    def commit_after_the_other_request():
        if not raced:
            raced.append(True)
            db.rollback()
            db.add(TheoryLessonCode(code="theirs01", lesson_key=NAV, part=1, created_at=datetime.now()))
            real_commit()
            db.add(TheoryLessonCode(code="ours0001", lesson_key=NAV, part=1, created_at=datetime.now()))
        real_commit()

    monkeypatch.setattr(db, "commit", commit_after_the_other_request)
    codes = theory_progress.lesson_codes(db)
    assert codes[(NAV, 1)] == "theirs01"


# ── cadet check-in ────────────────────────────────────────────────────────────

def test_a_cadet_scanning_a_lesson_is_checked_in(api, roster, db):
    code = code_for(api, NAV, 2)
    res = api.post(f"/cadets/me/lessons/{code}", headers=CADET)
    assert res.status_code == 200
    assert res.json() == {"key": NAV, "name": "Basic Navigation using Map and Compass", "category": "Leading",
                          "parts": 3, "part": 2, "already": False, "lessons": [2], "complete": False}
    row = db.query(CadetTheoryLesson).one()
    assert (row.cadet_id, row.part, row.recorded_by) == (1, 2, QR_CHECK_IN)


def test_scanning_twice_is_harmless_and_says_so(api, roster, db):
    code = code_for(api, NAV, 1)
    api.post(f"/cadets/me/lessons/{code}", headers=CADET)
    again = api.post(f"/cadets/me/lessons/{code}", headers=CADET).json()
    assert again["already"] is True and again["lessons"] == [1]
    assert db.query(CadetTheoryLesson).count() == 1


def test_the_last_lesson_scanned_makes_the_subject_taught(api, roster, db):
    mark(api, [1], [{"key": NAV, "part": 1}, {"key": NAV, "part": 2}])
    res = api.post(f"/cadets/me/lessons/{code_for(api, NAV, 3)}", headers=CADET).json()
    assert res["complete"] is True and res["lessons"] == [1, 2, 3]
    assert taught(db, 1, NAV).recorded_by == QR_CHECK_IN


def test_a_badge_lesson_is_complete_in_one_scan(api, roster, db):
    res = api.post(f"/cadets/me/lessons/{code_for(api, 'blue_first_aid', 1)}", headers=CADET).json()
    assert res["complete"] is True and res["parts"] == 1
    assert taught(db, 1, "blue_first_aid")


def test_a_scan_only_ever_checks_in_the_signed_in_cadet(api, roster, db):
    api.post(f"/cadets/me/lessons/{code_for(api, NAV, 1)}", headers=CADET)
    assert [r.cadet_id for r in db.query(CadetTheoryLesson)] == [1]


@pytest.mark.parametrize("code", ["nope", "acp_32_2", "x" * 200])
def test_an_unknown_code_is_a_clear_404(api, roster, db, code):
    res = api.post(f"/cadets/me/lessons/{code}", headers=CADET)
    assert res.status_code == 404 and "isn't a lesson" in res.json()["detail"]
    assert db.query(CadetTheoryLesson).count() == 0


def test_a_code_for_a_subject_no_longer_taught_is_404(api, roster, db):
    db.add_all([
        TheoryLessonCode(code="retired1", lesson_key="old_subject", part=1, created_at=datetime(2026, 1, 1)),
        TheoryLessonCode(code="toohigh1", lesson_key="blue_radio", part=2, created_at=datetime(2026, 1, 1)),
    ])
    db.commit()
    assert api.post("/cadets/me/lessons/retired1", headers=CADET).status_code == 404
    assert api.post("/cadets/me/lessons/toohigh1", headers=CADET).status_code == 404


def test_check_in_needs_a_token(api, roster):
    assert api.post("/cadets/me/lessons/abc").status_code == 401


@pytest.mark.parametrize("persona", ["staff", "nco", "cadet2"])
def test_someone_with_no_cadet_record_is_told_so(api, roster, db, persona):
    code = code_for(api, NAV, 1)
    res = api.post(f"/cadets/me/lessons/{code}", headers=api.as_(persona))
    assert res.status_code == 404 and "not registered" in res.json()["detail"]
    assert db.query(CadetTheoryLesson).count() == 0
