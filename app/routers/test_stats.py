"""The stats page's endpoints: bucketed history, per-flight cohorts, recent
badge awards and expiring qualifications. The original /stats/current and
snapshot tests live in test_events_stats_oc.py."""

from datetime import datetime, timedelta

import pytest

from database.models import Cadet, CadetQualification, StatsSnapshot

ENDPOINTS = ["/stats/current", "/stats/history", "/stats/awards", "/stats/expiring"]


def _today() -> datetime:
    now = datetime.now()
    return datetime(now.year, now.month, now.day)


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Zoë", last_name="Ó Briain", flight="A", classification="Leading Cadet"),
        Cadet(cin=2, first_name="Bea", last_name="Two", flight="B", classification="Junior Cadet"),
        Cadet(cin=3, first_name="Cy", last_name="Three", flight=None, classification=None),
    ])
    db.commit()


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ENDPOINTS)
def test_stats_endpoints_need_a_token(api, path):
    assert api.get(path).status_code == 401


@pytest.mark.parametrize("persona", ["cadet", "cadet2"])
@pytest.mark.parametrize("path", ENDPOINTS)
def test_cadets_cannot_read_stats(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc", "snco", "nco"])
@pytest.mark.parametrize("path", ENDPOINTS)
def test_ncos_and_above_can_read_stats(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 200


@pytest.mark.parametrize("query", ["days=0", "days=-5", "days=abc"])
@pytest.mark.parametrize("path", ["/stats/history", "/stats/awards"])
def test_a_bad_window_is_rejected(api, path, query):
    assert api.get(f"{path}?{query}", headers=api.as_("nco")).status_code == 422


# ── history ───────────────────────────────────────────────────────────────────

def test_history_keeps_the_last_snapshot_of_each_day(api, db):
    # Three manual scrapes on one day would otherwise plot three points on one date.
    day = _today() - timedelta(days=3)
    db.add_all([
        StatsSnapshot(captured_at=day.replace(hour=9), data={"n": 1}),
        StatsSnapshot(captured_at=day.replace(hour=12), data={"n": 2}),
        StatsSnapshot(captured_at=day.replace(hour=18), data={"n": 3}),
        StatsSnapshot(captured_at=day + timedelta(days=1, hours=10), data={"n": 4}),
    ])
    db.commit()
    body = api.get("/stats/history?days=30", headers=api.as_("nco")).json()
    assert [p["data"]["n"] for p in body] == [3, 4]


def test_long_ranges_bucket_by_week(api, db):
    # A Monday and the Sunday that ends its ISO week, then the next Monday.
    monday = _today() - timedelta(days=_today().weekday() + 21)
    db.add_all([
        StatsSnapshot(captured_at=monday + timedelta(hours=10), data={"n": 1}),
        StatsSnapshot(captured_at=monday + timedelta(days=6, hours=10), data={"n": 2}),
        StatsSnapshot(captured_at=monday + timedelta(days=7, hours=10), data={"n": 3}),
    ])
    db.commit()
    assert [p["data"]["n"] for p in api.get("/stats/history?days=365", headers=api.as_("nco")).json()] == [2, 3]
    # No window at all is the longest range, so it buckets weekly too.
    assert [p["data"]["n"] for p in api.get("/stats/history", headers=api.as_("nco")).json()] == [2, 3]
    # A short window stays daily.
    assert len(api.get("/stats/history?days=90", headers=api.as_("nco")).json()) == 3


def test_history_window_drops_older_snapshots(api, db):
    db.add_all([
        StatsSnapshot(captured_at=_today() - timedelta(days=200), data={"n": 1}),
        StatsSnapshot(captured_at=_today() - timedelta(days=10), data={"n": 2}),
    ])
    db.commit()
    assert [p["data"]["n"] for p in api.get("/stats/history?days=90", headers=api.as_("nco")).json()] == [2]


def test_no_snapshots_is_an_empty_history(api):
    assert api.get("/stats/history?days=90", headers=api.as_("nco")).json() == []


# ── per-flight cohorts ────────────────────────────────────────────────────────

def test_current_stats_break_badges_down_by_flight(api, db, squadron):
    db.add(CadetQualification(cadet_id=1, qual_type="Bronze Leadership", status="true"))
    db.commit()
    body = api.get("/stats/current", headers=api.as_("nco")).json()
    flights = body["flights"]
    assert set(flights) == {"A", "B", "Unknown"}
    assert flights["A"]["total_cadets"] == 1
    assert flights["A"]["badges"]["leadership"] == {"Bronze": 1}
    assert flights["A"]["non_junior"]["total_cadets"] == 1
    # Flight B's only cadet is a junior, so its non-junior cohort is empty.
    assert flights["B"]["non_junior"] == {"total_cadets": 0, "badges": {k: {} for k in body["badges"]}}
    # No classification counts as junior, same as the squadron-wide numbers.
    assert flights["Unknown"]["non_junior"]["total_cadets"] == 0


def test_an_empty_squadron_has_no_flights(api):
    assert api.get("/stats/current", headers=api.as_("nco")).json()["flights"] == {}


def test_snapshots_carry_the_flight_breakdown(api, db, squadron):
    api.post("/stats/snapshot", headers=api.as_("staff"))
    point = api.get("/stats/history", headers=api.as_("nco")).json()[0]
    assert point["data"]["flights"]["A"]["total_cadets"] == 1


# ── recent awards ─────────────────────────────────────────────────────────────

def test_recent_awards_lists_dashboard_badges_newest_first(api, db, squadron):
    now = datetime.now()
    db.add_all([
        CadetQualification(cadet_id=1, qual_type="Bronze Leadership", status="true",
                           date_achieved=now - timedelta(days=10)),
        CadetQualification(cadet_id=2, qual_type="Wing Musician (Bronze) - Drums", status="true",
                           date_achieved=now - timedelta(days=2)),
        # Not a dashboard badge.
        CadetQualification(cadet_id=1, qual_type="Instructor Cadet", status="true",
                           date_achieved=now - timedelta(days=1)),
        # Outside the window.
        CadetQualification(cadet_id=1, qual_type="Blue Leadership", status="true",
                           date_achieved=now - timedelta(days=60)),
        # No award date can't be placed in any window.
        CadetQualification(cadet_id=3, qual_type="Blue Shot (Air Rifle)", status="true"),
    ])
    db.commit()
    body = api.get("/stats/awards?days=30", headers=api.as_("nco")).json()
    assert [(a["cin"], a["badge"], a["level"]) for a in body] == [(2, "music", "Bronze"), (1, "leadership", "Bronze")]
    # "Musician" is a substring of every music tier; the highest-first match must win.
    assert body[0]["junior"] is True and body[0]["flight"] == "B"
    assert body[1]["name"] == "Zoë Ó Briain" and body[1]["junior"] is False


def test_awards_use_the_frontend_badge_keys(api, db, squadron):
    db.add(CadetQualification(cadet_id=1, qual_type="Basic Swimming Competence", status="true",
                              date_achieved=datetime.now()))
    db.commit()
    [award] = api.get("/stats/awards", headers=api.as_("nco")).json()
    assert (award["badge"], award["level"]) == ("swimming_proficiency", "Basic")


def test_no_recent_awards_is_an_empty_list(api, squadron):
    assert api.get("/stats/awards", headers=api.as_("nco")).json() == []


# ── expiring ──────────────────────────────────────────────────────────────────

def test_expiring_lists_the_next_three_months_soonest_first(api, db, squadron):
    today = _today()
    db.add_all([
        CadetQualification(cadet_id=1, qual_type="St John Activity First Aid", status="true",
                           date_expires=today + timedelta(days=60)),
        CadetQualification(cadet_id=3, qual_type="St John Youth First Aid", status="true",
                           date_expires=today + timedelta(days=5)),
        # Already lapsed, beyond the window, and never expiring are all left out.
        CadetQualification(cadet_id=2, qual_type="Old", status="true", date_expires=today - timedelta(days=1)),
        CadetQualification(cadet_id=2, qual_type="Far", status="true", date_expires=today + timedelta(days=200)),
        CadetQualification(cadet_id=2, qual_type="Forever", status="true"),
    ])
    db.commit()
    body = api.get("/stats/expiring", headers=api.as_("nco")).json()
    assert [(q["cin"], q["days_left"]) for q in body] == [(3, 5), (1, 60)]
    assert body[0]["flight"] == "Unknown" and body[0]["junior"] is True


def test_a_qual_expiring_today_is_still_listed(api, db, squadron):
    db.add(CadetQualification(cadet_id=1, qual_type="First Aid", status="true", date_expires=_today()))
    db.commit()
    [q] = api.get("/stats/expiring", headers=api.as_("nco")).json()
    assert q["days_left"] == 0
