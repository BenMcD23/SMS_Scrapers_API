"""Per-cadet stats history and what's built on it: the weekly snapshot, the
cadets drill-down, the classification funnel, intake retention, and targets."""

from datetime import date, datetime, timedelta

import pytest

import core.jobs as jobs
import routers.scrapers as scrapers
import routers.stats as stats
from database.models import Cadet, CadetQualification, CadetSnapshot, StatsSnapshot, StatsTarget


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Zoë", last_name="Ó Briain", flight="A", rank="Cpl", classification="Leading Cadet"),
        Cadet(cin=2, first_name="Bea", last_name="Two", flight="B", classification="Junior Cadet"),
        Cadet(cin=3, first_name="adam", last_name="Three", flight=None, classification=None),
    ])
    db.add_all([
        CadetQualification(cadet_id=1, qual_type="Bronze Leadership", status="true"),
        CadetQualification(cadet_id=1, qual_type="Blue Leadership", status="true"),
        CadetQualification(cadet_id=2, qual_type="Blue Leadership", status="true"),
    ])
    db.commit()


def _snapshot(db, at: datetime, cadets: list[dict]) -> StatsSnapshot:
    """A snapshot with per-cadet rows, as save_snapshot would have left it."""
    snap = StatsSnapshot(captured_at=at, data={})
    db.add(snap)
    db.flush()
    for c in cadets:
        db.add(CadetSnapshot(
            snapshot_id=snap.id,
            cin=c["cin"],
            name=c.get("name", f"Cadet {c['cin']}"),
            flight=c.get("flight", "A"),
            rank=c.get("rank"),
            classification=c.get("classification"),
            junior=c.get("classification") in (None, "Junior Cadet"),
            badges=c.get("badges", {}),
        ))
    db.commit()
    return snap


# ── access ────────────────────────────────────────────────────────────────────

READS = ["/stats/cadets", "/stats/funnel", "/stats/retention", "/stats/targets", "/stats/badge-levels"]


@pytest.mark.parametrize("path", READS)
def test_history_reads_need_a_token(api, path):
    assert api.get(path).status_code == 401


@pytest.mark.parametrize("persona", ["cadet", "cadet2"])
@pytest.mark.parametrize("path", READS)
def test_cadets_cannot_read_history(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc", "snco", "nco"])
@pytest.mark.parametrize("path", READS)
def test_ncos_and_above_can_read_history(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 200


# ── per-cadet snapshots ───────────────────────────────────────────────────────

def test_a_snapshot_records_every_cadet_as_they_stand(api, db, squadron):
    api.post("/stats/snapshot", headers=api.as_("staff"))
    rows = {r.cin: r for r in db.query(CadetSnapshot).all()}
    assert set(rows) == {1, 2, 3}
    assert rows[1].name == "Zoë Ó Briain" and rows[1].rank == "Cpl" and rows[1].junior is False
    # Highest level held, under the frontend's badge key.
    assert rows[1].badges == {"leadership": "Bronze"}
    assert rows[3].junior is True and rows[3].badges == {}


def test_the_scraper_snapshot_records_cadets_too(db, squadron):
    scrapers._save_stats_snapshot(db)
    assert db.query(StatsSnapshot).count() == 1
    assert db.query(CadetSnapshot).count() == 3


def test_a_failed_snapshot_after_a_scrape_saves_nothing_and_doesnt_raise(db, squadron, monkeypatch):
    def boom(_db):
        raise RuntimeError("disk full")
    monkeypatch.setattr(stats, "compute_stats", boom)
    scrapers._save_stats_snapshot(db)
    assert db.query(StatsSnapshot).count() == 0 and db.query(CadetSnapshot).count() == 0


def test_a_departed_cadets_history_outlives_them(api, db, squadron):
    # The scraper deletes cadets who've left; retention needs their history.
    api.post("/stats/snapshot", headers=api.as_("staff"))
    db.query(CadetQualification).filter(CadetQualification.cadet_id == 2).delete()
    db.query(Cadet).filter(Cadet.cin == 2).delete()
    db.commit()
    assert db.query(CadetSnapshot).filter(CadetSnapshot.cin == 2).count() == 1


def test_an_empty_squadron_snapshots_with_no_cadet_rows(api, db):
    assert api.post("/stats/snapshot", headers=api.as_("staff")).status_code == 200
    assert db.query(StatsSnapshot).count() == 1 and db.query(CadetSnapshot).count() == 0


# ── weekly job ────────────────────────────────────────────────────────────────

def test_weekly_snapshot_fills_a_week_nobody_scraped(db, session_factory, squadron):
    db.add(StatsSnapshot(captured_at=_now() - timedelta(days=8), data={}))
    db.commit()
    jobs.weekly_stats_snapshot()
    assert db.query(StatsSnapshot).count() == 2
    assert db.query(CadetSnapshot).count() == 3


def test_weekly_snapshot_skips_a_week_that_already_has_one(db, session_factory, squadron):
    now = _now()
    monday = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
    db.add(StatsSnapshot(captured_at=monday + timedelta(minutes=1), data={}))
    db.commit()
    jobs.weekly_stats_snapshot()
    assert db.query(StatsSnapshot).count() == 1


def test_weekly_snapshot_failure_is_logged_not_raised(db, session_factory, monkeypatch, caplog):
    def boom(_db):
        raise RuntimeError("db gone")
    monkeypatch.setattr(jobs, "save_snapshot", boom)
    jobs.weekly_stats_snapshot()
    assert "weekly stats snapshot failed" in caplog.text


def test_weekly_snapshot_is_scheduled(monkeypatch):
    added = []

    class Sched:
        def add_job(self, func, trigger=None, **kw):
            added.append((func.__name__, str(trigger)))

    monkeypatch.setattr(jobs.scrapers, "register_schedule_jobs", lambda: None)
    jobs.register_jobs(Sched())
    [(_, trigger)] = [a for a in added if a[0] == "weekly_stats_snapshot"]
    assert "day_of_week='sun'" in trigger


# ── drill-down ────────────────────────────────────────────────────────────────

def _names(res) -> list[str]:
    return [c["name"] for c in res.json()["cadets"]]


def test_drill_down_lists_who_holds_a_badge_at_a_level(api, squadron):
    res = api.get("/stats/cadets?badge=leadership&level=Blue", headers=api.as_("nco"))
    assert res.json()["as_of"] is None
    assert _names(res) == ["Bea Two"]
    assert res.json()["cadets"][0]["level"] == "Blue"


def test_drill_down_none_lists_who_doesnt_hold_it(api, squadron):
    assert _names(api.get("/stats/cadets?badge=leadership&level=None", headers=api.as_("nco"))) == ["adam Three"]


def test_drill_down_sorts_names_ignoring_case(api, squadron):
    assert _names(api.get("/stats/cadets", headers=api.as_("nco"))) == ["adam Three", "Bea Two", "Zoë Ó Briain"]


def test_drill_down_by_classification_flight_and_juniors(api, squadron):
    nco = api.as_("nco")
    # No classification recorded counts as Junior, the same as the charts.
    assert _names(api.get("/stats/cadets?classification=Junior Cadet", headers=nco)) == ["adam Three", "Bea Two"]
    assert _names(api.get("/stats/cadets?flight=Unknown", headers=nco)) == ["adam Three"]
    assert _names(api.get("/stats/cadets?exclude_juniors=true", headers=nco)) == ["Zoë Ó Briain"]
    assert _names(api.get("/stats/cadets?flight=A&badge=leadership&level=Blue", headers=nco)) == []


def test_drill_down_on_a_past_day_reads_that_weeks_snapshot(api, db, squadron):
    then = _now() - timedelta(days=30)
    _snapshot(db, then, [{"cin": 9, "name": "Gone Cadet", "badges": {"leadership": "Blue"}}])
    _snapshot(db, then + timedelta(days=7), [{"cin": 9, "name": "Gone Cadet", "badges": {"leadership": "Gold"}}])
    day = (then + timedelta(days=3)).date().isoformat()
    res = api.get(f"/stats/cadets?badge=leadership&level=Blue&on={day}", headers=api.as_("nco"))
    assert _names(res) == ["Gone Cadet"]
    assert res.json()["as_of"].startswith(then.date().isoformat())


def test_drill_down_skips_snapshots_without_cadet_rows(api, db, squadron):
    # Snapshots from before per-cadet history only hold totals.
    then = _now() - timedelta(days=30)
    _snapshot(db, then, [{"cin": 9, "name": "Old Row"}])
    db.add(StatsSnapshot(captured_at=then + timedelta(days=5), data={}))
    db.commit()
    day = (then + timedelta(days=6)).date().isoformat()
    assert _names(api.get(f"/stats/cadets?on={day}", headers=api.as_("nco"))) == ["Old Row"]


def test_drill_down_before_any_history_is_a_404(api, db, squadron):
    _snapshot(db, _now() - timedelta(days=5), [{"cin": 9}])
    day = (_now() - timedelta(days=30)).date().isoformat()
    assert api.get(f"/stats/cadets?on={day}", headers=api.as_("nco")).status_code == 404


def test_drill_down_on_today_is_live(api, squadron):
    res = api.get(f"/stats/cadets?on={date.today().isoformat()}", headers=api.as_("nco"))
    assert res.json()["as_of"] is None and len(res.json()["cadets"]) == 3


def test_drill_down_by_reached_classification_includes_those_beyond_it(api, db, squadron):
    db.add(Cadet(cin=4, first_name="Max", last_name="Master", classification="Master Air Cadet"))
    db.commit()
    nco = api.as_("nco")
    assert _names(api.get("/stats/cadets?min_classification=Leading Cadet", headers=nco)) == ["Max Master", "Zoë Ó Briain"]
    assert len(api.get("/stats/cadets?min_classification=Junior Cadet", headers=nco).json()["cadets"]) == 4


@pytest.mark.parametrize("query, why", [
    ("min_classification=Wizard", "Unknown classification"),
    ("badge=leadership", "together"),
    ("level=Blue", "together"),
    ("badge=knitting&level=Blue", "Unknown badge"),
    ("badge=leadership&level=Platinum", "Unknown level"),
])
def test_drill_down_rejects_bad_filters(api, query, why):
    res = api.get(f"/stats/cadets?{query}", headers=api.as_("nco"))
    assert res.status_code == 400 and why in res.json()["detail"]


def test_drill_down_rejects_a_malformed_day(api):
    assert api.get("/stats/cadets?on=last-week", headers=api.as_("nco")).status_code == 422


# ── funnel ────────────────────────────────────────────────────────────────────

def test_funnel_counts_who_has_reached_each_step(api, squadron):
    body = api.get("/stats/funnel", headers=api.as_("nco")).json()
    assert body["total"] == 3
    assert [s["reached"] for s in body["steps"]] == [3, 1, 1, 0, 0]
    assert [s["pct_of_previous"] for s in body["steps"]] == [None, 33, 100, 0, None]


def test_funnel_times_only_steps_it_saw_happen(api, db, squadron):
    t0 = _now() - timedelta(days=200)
    week = timedelta(days=7)
    # Cadet 5 is seen joining as a junior, reaching First Class 4 weeks later
    # and Leading 10 weeks after that.
    _snapshot(db, t0, [{"cin": 5, "classification": None}, {"cin": 6, "classification": "First Class Cadet"}])
    _snapshot(db, t0 + 4 * week, [{"cin": 5, "classification": "First Class Cadet"}, {"cin": 6, "classification": "Leading Cadet"}])
    _snapshot(db, t0 + 14 * week, [{"cin": 5, "classification": "Leading Cadet"}, {"cin": 6, "classification": "Leading Cadet"}])
    steps = {s["name"]: s for s in api.get("/stats/funnel", headers=api.as_("nco")).json()["steps"]}
    # First Class: only cadet 5 was seen arriving at the step before it... but
    # Junior is where everyone starts, so nobody "arrives" at Junior: untimed.
    assert steps["First Class Cadet"]["median_days_from_previous"] is None
    # Leading: cadet 5 arrived at First Class and then at Leading, 70 days apart.
    # Cadet 6 was already First Class when tracking began, so doesn't count.
    assert steps["Leading Cadet"]["median_days_from_previous"] == 70
    assert steps["Leading Cadet"]["timed_cadets"] == 1


def test_funnel_can_be_narrowed_to_a_flight(api, squadron):
    body = api.get("/stats/funnel?flight=A", headers=api.as_("nco")).json()
    assert body["total"] == 1 and body["steps"][2]["reached"] == 1


def test_funnel_for_an_empty_squadron(api):
    body = api.get("/stats/funnel", headers=api.as_("nco")).json()
    assert body["total"] == 0 and all(s["reached"] == 0 for s in body["steps"])
    assert all(s["pct_of_previous"] is None for s in body["steps"])


# ── retention ─────────────────────────────────────────────────────────────────

def test_retention_groups_intakes_and_leaves_out_who_was_already_there(api, db):
    t0 = datetime(2025, 1, 6, 21)
    weeks = [t0 + timedelta(days=7 * i) for i in range(60)]
    for i, at in enumerate(weeks):
        cadets = [{"cin": 1}]                                  # on strength before tracking began
        if i >= 5:
            cadets.append({"cin": 2})                          # joined Feb 2025, stays
            if i < 20:
                cadets.append({"cin": 3})                      # joined Feb 2025, left after ~3 months
        _snapshot(db, at, cadets)
    db.add(Cadet(cin=2, first_name="Still", last_name="Here"))
    db.commit()
    body = api.get("/stats/retention", headers=api.as_("nco")).json()
    assert body == [{"intake": "2025-02", "joined": 2, "still_on_strength": 1, "6m": 1, "12m": 1}]


def test_retention_marks_are_null_until_the_intake_reaches_them(api, db):
    t0 = _now() - timedelta(days=100)
    _snapshot(db, t0, [{"cin": 1}])
    _snapshot(db, t0 + timedelta(days=7), [{"cin": 1}, {"cin": 2}])
    _snapshot(db, t0 + timedelta(days=99), [{"cin": 1}, {"cin": 2}])
    [row] = api.get("/stats/retention", headers=api.as_("nco")).json()
    assert row["joined"] == 1 and row["6m"] is None and row["12m"] is None


def test_retention_with_no_history_is_empty(api):
    assert api.get("/stats/retention", headers=api.as_("nco")).json() == []


# ── targets ───────────────────────────────────────────────────────────────────

TARGET = {"badge": "first_aid", "min_level": "Silver", "target_pct": 80, "due": "2027-07-01", "exclude_juniors": True}


def test_staff_create_targets_and_ncos_read_them(api, db):
    res = api.post("/stats/targets", headers=api.as_("staff"), json=TARGET)
    assert res.status_code == 201
    body = res.json()
    # Silver or better: the levels that count toward it.
    assert body["levels"] == ["Silver", "Gold"]
    assert body["created_by"] == "staff@317atc.co.uk"
    assert api.get("/stats/targets", headers=api.as_("nco")).json() == [body]


def test_a_target_with_no_minimum_counts_every_level(api):
    body = api.post("/stats/targets", headers=api.as_("staff"), json={**TARGET, "min_level": None}).json()
    assert body["levels"] == ["Blue", "Bronze", "Silver", "Gold"]


def test_targets_are_listed_soonest_due_first(api):
    for due in ["2027-09-01", "2027-01-01"]:
        api.post("/stats/targets", headers=api.as_("staff"), json={**TARGET, "due": due})
    assert [t["due"] for t in api.get("/stats/targets", headers=api.as_("nco")).json()] == ["2027-01-01", "2027-09-01"]


@pytest.mark.parametrize("persona", ["nco", "snco", "cadet"])
def test_only_staff_change_targets(api, db, persona):
    db.add(StatsTarget(badge="first_aid", target_pct=50, due=date(2027, 1, 1), created_at=_now()))
    db.commit()
    who = api.as_(persona)
    assert api.post("/stats/targets", headers=who, json=TARGET).status_code == 403
    assert api.put("/stats/targets/1", headers=who, json=TARGET).status_code == 403
    assert api.delete("/stats/targets/1", headers=who).status_code == 403
    assert api.post("/stats/targets", json=TARGET).status_code == 401


def test_staff_edit_and_delete_targets(api):
    staff = api.as_("staff")
    tid = api.post("/stats/targets", headers=staff, json=TARGET).json()["id"]
    res = api.put(f"/stats/targets/{tid}", headers=staff, json={**TARGET, "target_pct": 60, "flight": "B"})
    assert res.json()["target_pct"] == 60 and res.json()["flight"] == "B"
    assert api.delete(f"/stats/targets/{tid}", headers=staff).status_code == 204
    assert api.get("/stats/targets", headers=staff).json() == []


@pytest.mark.parametrize("method", ["put", "delete"])
def test_a_missing_target_is_a_404(api, method):
    kwargs = {"json": TARGET} if method == "put" else {}
    assert getattr(api, method)("/stats/targets/99", headers=api.as_("staff"), **kwargs).status_code == 404


@pytest.mark.parametrize("change, why", [
    ({"badge": "knitting"}, "Unknown badge"),
    ({"badge": "swimming_proficiency", "min_level": "Gold"}, "isn't a level"),
])
def test_a_target_for_a_badge_or_level_that_doesnt_exist_is_rejected(api, change, why):
    res = api.post("/stats/targets", headers=api.as_("staff"), json={**TARGET, **change})
    assert res.status_code == 400 and why in res.json()["detail"]


@pytest.mark.parametrize("change", [{"target_pct": 0}, {"target_pct": 101}, {"due": "soon"}, {"badge": None}])
def test_a_malformed_target_is_a_422(api, change):
    assert api.post("/stats/targets", headers=api.as_("staff"), json={**TARGET, **change}).status_code == 422


def test_badge_levels_come_from_the_catalog_lowest_first(api):
    body = api.get("/stats/badge-levels", headers=api.as_("nco")).json()
    assert body["first_aid"] == ["Blue", "Bronze", "Silver", "Gold"]
    # Ladders that differ: no Blue for Cyber, Nijmegen above Gold, swimming's own names.
    assert body["cyber"] == ["Bronze", "Silver", "Gold"]
    assert body["road_marching"][-1] == "Nijmegen"
    assert body["swimming_proficiency"] == ["Basic", "Intermediate", "Advanced"]
    # Every leveled badge the stats count, ATP Ground School included.
    assert set(body) == stats.STAT_BADGE_KEYS
