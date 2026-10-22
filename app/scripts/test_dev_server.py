"""The local preview seed (scripts/dev_server.py). It's what every UI preview
and screenshot runs against, so it has to keep working as the models move."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.security as security
from core.qualifications import BADGE_TYPES
from database.database import Base
from database.models import Cadet, CadetAttendance, CadetFlight, CadetQualification, CadetTheoryProgress, Staff
from scripts.dev_server import (
    CADET_COUNT,
    INSPECTION_NIGHTS,
    LAPSED_CADETS,
    LAPSED_WEEKS,
    PARADE_NIGHTS,
    _parade_nights,
    schema_is_stale,
    seed,
    seed_inspections,
    seed_recruits,
    seed_stores,
    seed_stores_history,
)

NOW = datetime(2026, 10, 8, 18, 0)  # a Thursday


def fresh_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng)()


def test_seeds_a_squadron_into_an_empty_database(db):
    assert seed(db, now=NOW) is True
    assert db.query(Cadet).count() == CADET_COUNT
    # Everyone has every night except the lapsed cadets, who miss the last LAPSED_WEEKS.
    missed = len(LAPSED_CADETS) * LAPSED_WEEKS
    assert db.query(CadetAttendance).count() == CADET_COUNT * PARADE_NIGHTS - missed
    assert db.query(Cadet).filter(Cadet.banned).count() == 1
    assert db.query(Cadet).filter(Cadet.missing_since.isnot(None)).count() == 1  # the overview banner
    # Flights are stored as the letter, as the scraper stores them.
    assert {c.flight for c in db.query(Cadet)} <= {"A", "B", "C"}


def test_never_touches_a_database_that_already_has_cadets(db):
    # Someone's been clicking around in it; a restart must not add a second squadron.
    db.add(Cadet(cin=1, first_name="Kept", last_name="Mine"))
    db.commit()
    assert seed(db, now=NOW) is False
    assert [c.first_name for c in db.query(Cadet)] == ["Kept"]


def test_the_dev_logins_are_on_the_roster(db):
    seed(db, now=NOW)
    emails = {c.email: c.rank for c in db.query(Cadet)}
    assert emails[security._dev_fake_email("snco")] == "Sgt"
    assert emails[security._dev_fake_email("nco")] == "Cpl"
    # The dev staff login is the owner; Settings needs a staff record to link to.
    assert db.query(Staff).filter(Staff.email == security.OWNER_EMAIL).count() == 1


def test_every_seeded_qualification_counts_toward_a_dashboard_badge(db):
    # A made-up name ("first_aid") would leave the badge bars at 0% and the
    # expiring list showing raw keys — the preview would look broken when it isn't.
    seed(db, now=NOW)
    levels = [lvl for b in BADGE_TYPES for lvl in b.levels]
    quals = db.query(CadetQualification).all()
    assert quals
    for q in quals:
        assert any(lvl.matches(q.qual_type) for lvl in levels), q.qual_type


def test_first_aid_expiries_include_lapsed_and_upcoming(db):
    seed(db, now=NOW)
    expiries = [q.date_expires for q in db.query(CadetQualification) if q.date_expires]
    assert any(d < NOW for d in expiries)
    assert any(d > NOW for d in expiries)


def test_is_the_same_squadron_every_time():
    a, b = fresh_session(), fresh_session()
    seed(a, now=NOW)
    seed(b, now=NOW)
    names = lambda s: [(c.cin, c.first_name, c.last_name, c.rank) for c in s.query(Cadet).order_by(Cadet.cin)]  # noqa: E731
    assert names(a) == names(b)


def test_parade_nights_are_the_mondays_before_today():
    nights = _parade_nights(NOW, 3)
    assert [n.date().isoformat() for n in nights] == ["2026-09-21", "2026-09-28", "2026-10-05"]
    # On a Monday, that Monday hasn't been registered yet.
    assert _parade_nights(datetime(2026, 10, 5, 9), 1)[0].date().isoformat() == "2026-09-28"


def test_stores_seed_goes_through_the_api(api, db):
    seed(db, now=NOW)
    staff = api.as_("staff")
    seed_stores(api, staff)

    orders = api.get("/stores/orders", headers=staff).json()
    assert len(orders) == 4
    assert all(not o["completed"] for o in orders)
    assert any(i["needSizing"] for o in orders for i in o["items"])
    assert api.get("/stores/stock", headers=staff).json()
    assert len(api.get("/stores/badges/orders", headers=staff).json()) == 1
    # Cadets with orders have sizes on record too, for the orders page's Sizes popover.
    issued = [api.get(f"/stores/issuances/{o['cadetCin']}", headers=staff).json() for o in orders]
    assert sum(1 for i in issued if i) == 3


def test_stores_history_gives_the_insights_page_past_demand(api, db):
    seed(db, now=NOW)
    staff = api.as_("staff")
    seed_stores(api, staff)
    seed_stores_history(api, staff, db, now=NOW)

    orders = api.get("/stores/orders", headers=staff).json()
    past = [o for o in orders if o["completed"]]
    assert len(past) == 70
    assert all(i["givenAt"] for o in past for i in o["items"])
    # Spread over the last year and a bit, never in the future.
    stamps = [datetime.fromisoformat(o["timestamp"]) for o in past]
    assert min(stamps) >= NOW - timedelta(days=451) and max(stamps) <= NOW

    insights = api.get("/stores/insights", headers=staff).json()
    assert insights["summary"]["requested"] > 50
    assert insights["toOrder"] and insights["unused"]


def test_the_portal_data_gives_every_progress_card_something_to_show(api, db):
    # The stats page's Blue Flying, exam and time-served cards read this.
    seed(db, now=NOW)
    assert db.query(CadetFlight).count() > 0
    assert db.query(CadetTheoryProgress).count() > 0
    assert all(c.joined_on for c in db.query(Cadet))
    for c in db.query(Cadet):
        # A classification date for every classification reached, in order.
        dates = list((c.classification_dates or {}).values())
        assert dates == sorted(dates)
        assert bool(dates) == bool(c.classification)
    body = api.get("/stats/progress", headers=api.as_("staff")).json()
    assert sum(1 for n in body["blue_flying"].values() if n) >= 4  # most pipeline states occur
    assert body["flown_last_year"] > 0


def test_the_classifications_page_has_lessons_part_done_and_taught_subjects_whole(api, db):
    # A taught subject has every lesson, as the real recording keeps it; some
    # cadets are part-way so the page shows "2 of 3", some by QR check-in.
    from core.theory_lessons import THEORY_LESSON_BY_KEY
    from core.theory_progress import PORTAL, QR_CHECK_IN
    from database.models import CadetTheoryLesson

    seed(db, now=NOW)
    had = {}
    for row in db.query(CadetTheoryLesson):
        had.setdefault((row.cadet_id, row.lesson_key), set()).add(row.part)
    taught = {(r.cadet_id, r.lesson_key) for r in db.query(CadetTheoryProgress) if r.recorded_by != PORTAL}
    for key, parts in had.items():
        whole = parts == set(range(1, THEORY_LESSON_BY_KEY[key[1]].parts + 1))
        assert whole == (key in taught), key
    assert taught <= set(had)
    assert any(len(parts) < 3 for parts in had.values())
    assert db.query(CadetTheoryLesson).filter_by(recorded_by=QR_CHECK_IN).count() > 0

    rows = api.get("/cadets/theory/progress", headers=api.as_("staff")).json()
    assert any(s["lessons"] and not s["taught"] for r in rows for s in r["subjects"].values())


def test_the_leaving_process_has_lapsed_cadets_and_parent_emails_to_show(db):
    seed(db, now=NOW)
    from core.leaving import gap_days, is_lapsed
    from routers.leaving import _current_parade_night, _last_attended_by_cadet

    current, last = _current_parade_night(db), _last_attended_by_cadet(db)
    lapsed = [c.cin for c in db.query(Cadet) if is_lapsed(last.get(c.cin), current)]
    assert len(lapsed) == len(LAPSED_CADETS)
    assert all(gap_days(last[cin], current) >= 56 for cin in lapsed)

    parents = [c.parent_emails for c in db.query(Cadet)]
    assert any(parents) and not all(parents)  # some on file, some missing


def test_flying_paperwork_has_a_started_an_untouched_and_a_finished_event(db):
    from database.models import FlyingEvent
    from scripts.flying_paperwork import event_has_ticks

    seed(db, now=NOW)
    events = {e.title: e for e in db.query(FlyingEvent)}
    assert len(events) == 3
    assert [event_has_ticks(e) for e in events.values()] == [True, False, True]
    assert [e.on_bader for e in events.values()] == [True, True, False]
    assert any(not r.on_event for e in events.values() for r in e.cadets)


def test_inspections_give_the_audit_a_pattern_to_find(api, db):
    # Each flight was seeded with a fault the audit should rank top, and B's and
    # A's should trend the way they were seeded.
    seed(db, now=NOW)
    staff = api.as_("staff")
    seed_inspections(api, staff, now=NOW)

    sheets = api.get("/inspections/sheets", headers=staff).json()
    assert len(sheets) == INSPECTION_NIGHTS
    assert {s["uniform"] for s in sheets} == {"blues", "mtp"}

    squadron = api.get("/inspections/audit", headers=staff).json()
    assert [f["flight"] for f in squadron["flights"]] == ["A", "B", "C"]
    assert squadron["summary"]["awol"] > 0 and squadron["positives"]

    def top(flight):
        return api.get(f"/inspections/audit?scope=flight&flight={flight}", headers=staff).json()["faults"]

    a, b, c = top("A"), top("B"), top("C")
    assert a[0]["region"] == "Beret / Headdress" and a[0]["trend"] == "better"
    assert b[0]["region"] == "Shoes" and b[0]["trend"] == "worse" and b[0]["persistent_cadets"] > 0
    assert len(b[0]["texts"]) > 1
    assert c[0]["region"] == "Hair / Face"


def test_the_classification_day_planner_has_every_group_to_show(api, db):
    seed(db, now=NOW)
    db.commit()
    body = api.get("/classification-day", params={"date": NOW.date().isoformat()},
                   headers=api.as_("staff")).json()
    kinds = {c["kind"] for c in body["cadets"]}
    assert {"exam", "lesson", "first_class", "complete"} <= kinds
    assert any(c["attendance"]["flags"] for c in body["cadets"])


def test_a_database_missing_a_model_column_is_flagged_stale():
    # A preview db seeded before a model gained a column would 500 on every query.
    engine = create_engine("sqlite://", poolclass=StaticPool)
    assert not schema_is_stale(engine, Base)  # empty: create_all will build it
    Base.metadata.create_all(engine)
    assert not schema_is_stale(engine, Base)
    with engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE "Cadets" DROP COLUMN parent_emails')
    assert schema_is_stale(engine, Base)


def test_recruits_cover_every_stage_of_the_joining_form(api, db):
    staff = api.as_("staff")
    seed_recruits(api, staff, db)

    rows = {r["cadetFirstName"]: r for r in api.get("/recruits", headers=staff).json()}
    assert rows["Erin"]["status"] == "submitted"
    assert rows["Kian"]["status"] == "in_progress" and 0 < rows["Kian"]["progress"]["done"] < 8
    assert rows["Maisie"]["status"] == "not_started"
    assert rows["Oscar"]["expired"] is True
    # The submitted one has a health declaration, so both PDFs open.
    for form in ("3822A", "3822H"):
        res = api.get(f"/recruits/{rows['Erin']['id']}/pdf?form={form}", headers=staff)
        assert res.status_code == 200


def test_activity_logs_give_the_portal_and_the_dashboard_every_state(api, db, monkeypatch):
    import routers.activity_logs
    from database.models import ActivityLog, ActivityLogEnrolment

    monkeypatch.setattr(routers.activity_logs, "_today", lambda: NOW.date())
    seed(db, now=NOW)
    nco = db.query(Cadet).filter(Cadet.email == security._dev_fake_email("nco")).one()
    snco = db.query(Cadet).filter(Cadet.email == security._dev_fake_email("snco")).one()
    # The NCO login sees the page; the SNCO login is the "not switched on" path.
    assert db.get(ActivityLogEnrolment, nco.cin) and not db.get(ActivityLogEnrolment, snco.cin)
    assert db.query(ActivityLog).filter(ActivityLog.completed_at.isnot(None)).count() >= 1

    body = api.get("/activity-logs/weekly", headers=api.as_("staff")).json()
    statuses = {s["status"] for c in body["cadets"] for s in c["sections"]}
    assert {"done", "none", "not_set_up", "complete"} <= statuses
    assert any(c["behind"] for c in body["cadets"]) and not all(c["behind"] for c in body["cadets"])
