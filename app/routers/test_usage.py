from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from conftest import OWNER, PERSONAS
from core import usage
from database.models import ScraperRun, UsageDaily
from routers import usage as usage_router

OWNER_H = {"Authorization": "Bearer owner"}


def counters(db):
    db.expire_all()
    return {(r.method, r.route, r.email): r.calls for r in db.query(UsageDaily).all()}


# ── access ────────────────────────────────────────────────────────────────────

def test_usage_needs_a_token(api):
    assert api.get("/usage").status_code == 401


@pytest.mark.parametrize("persona", ["staff", "snco", "nco", "cadet", "oc"])
def test_usage_is_owner_only(api, persona):
    # The OC is staff too — still not the developer.
    assert api.get("/usage", headers=api.as_(persona)).status_code == 403


def test_owner_can_read_usage(api):
    assert api.get("/usage", headers=OWNER_H).status_code == 200


@pytest.mark.parametrize("days", [0, -1, usage_router.USAGE_RETENTION_DAYS + 1, "lots"])
def test_usage_window_is_validated(api, days):
    assert api.get(f"/usage?days={days}", headers=OWNER_H).status_code == 422


# ── counting ──────────────────────────────────────────────────────────────────

def test_calls_are_counted_per_route_template_and_caller(api, db):
    run = ScraperRun(scraper_id="staff", ran_at=datetime.now(), success=True)
    db.add(run)
    db.commit()
    for _ in range(3):
        assert api.get(f"/scraper-runs/{run.id}", headers=api.as_("staff")).status_code == 200
    assert api.get("/reference", headers=api.as_("cadet")).status_code == 200

    # The template, not /scraper-runs/1 — otherwise every cadet/order id would
    # be its own "feature".
    assert counters(db) == {
        ("GET", "/scraper-runs/{run_id}", PERSONAS["staff"]["email"]): 3,
        ("GET", "/reference", PERSONAS["cadet"]["email"]): 1,
    }


def test_failed_and_unmatched_calls_are_not_counted(api, db):
    assert api.get("/scraper-runs/99999", headers=api.as_("staff")).status_code == 404
    assert api.get("/scraper-runs/1", headers=api.as_("cadet")).status_code == 403
    assert api.get("/reference").status_code == 401
    assert api.get("/no-such-page", headers=api.as_("staff")).status_code == 404
    assert counters(db) == {}


def test_probes_health_and_the_usage_page_itself_are_not_counted(api, db):
    for path in ("/ping", "/healthz", "/health"):
        assert api.get(path, headers=api.as_("staff")).status_code == 200
    assert api.get("/usage", headers=OWNER_H).status_code == 200
    assert counters(db) == {}


def test_a_broken_counter_never_fails_the_request(api, db, monkeypatch):
    def down():
        raise OperationalError("SELECT 1", {}, Exception("database is gone"))

    monkeypatch.setattr(usage, "SessionLocal", down)
    assert api.get("/reference", headers=api.as_("staff")).status_code == 200


def test_a_racing_insert_increments_the_existing_row(db, session_factory, monkeypatch):
    now = datetime(2026, 10, 6, 12)
    usage.record("GET", "/reference", "a@x", now=now)

    # The first session misses the row (as if another request inserted it
    # after our read), so its insert hits the unique constraint; the retry
    # must find the row and increment it rather than dropping the count.
    sessions = []

    def factory():
        s = session_factory()
        if not sessions:
            real_query = s.query

            class Miss:
                def filter_by(self, **kw):
                    return self

                def first(self):
                    return None

            s.query = lambda *a, **kw: Miss() if a == (UsageDaily,) else real_query(*a, **kw)
        sessions.append(s)
        return s

    monkeypatch.setattr(usage, "SessionLocal", factory)
    usage.record("GET", "/reference", "a@x", now=now)
    assert len(sessions) == 2
    assert counters(db) == {("GET", "/reference", "a@x"): 2}


def test_emails_are_lowercased_so_one_person_is_one_user():
    holder = usage.start_request()
    usage.note_caller("Ben.McDonald@317ATC.co.uk")
    assert holder == {"email": "ben.mcdonald@317atc.co.uk"}
    # Outside a request there is no holder, and noting is a harmless no-op.
    usage._caller.set(None)
    usage.note_caller("x@y")


# ── report ────────────────────────────────────────────────────────────────────

def seed(db, rows):
    db.add_all([
        UsageDaily(day=day, method=m, route=route, email=email, calls=calls,
                   last_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=12))
        for day, m, route, email, calls in rows
    ])
    db.commit()


def test_report_ranks_routes_and_users_and_lists_what_nobody_used(api, db):
    today = date.today()
    staff, cadet = PERSONAS["staff"]["email"], PERSONAS["cadet"]["email"]
    seed(db, [
        (today, "GET", "/reference", staff, 5),
        (today - timedelta(days=1), "GET", "/reference", cadet, 2),
        (today, "GET", "/reference", "", 1),
        (today, "POST", "/stores/orders", staff, 1),
        # Older than the 30-day window — must not count.
        (today - timedelta(days=40), "GET", "/stats/current", staff, 99),
    ])
    body = api.get("/usage", headers=OWNER_H).json()

    assert body["days"] == 30 and body["since"] == (today - timedelta(days=29)).isoformat()
    assert body["total_calls"] == 9
    ref, orders = body["routes"]
    assert ref == {
        "method": "GET", "route": "/reference", "calls": 8, "users": 2,
        "roles": {"staff": 5, "cadet": 2, "anonymous": 1},
        "last_used": f"{today.isoformat()}T12:00:00",
    }
    assert (orders["route"], orders["calls"], orders["users"]) == ("/stores/orders", 1, 1)

    assert [(u["email"], u["role"], u["calls"], u["routes"]) for u in body["users"]] == [
        (staff, "staff", 6, 2), (cadet, "cadet", 2, 1),
    ]

    unused = {(u["method"], u["route"]) for u in body["unused"]}
    assert ("GET", "/stats/current") in unused  # only used outside the window
    assert ("GET", "/reference") not in unused and ("POST", "/stores/orders") not in unused
    # Nothing that's skipped on purpose is reported as "unused" either.
    assert not {r for _, r in unused} & usage.SKIP_ROUTES
    assert all(m not in ("HEAD", "OPTIONS") for m, _ in unused)


def test_a_wider_window_includes_older_use(api, db):
    seed(db, [(date.today() - timedelta(days=40), "GET", "/stats/current", OWNER, 4)])
    assert api.get("/usage?days=30", headers=OWNER_H).json()["total_calls"] == 0
    body = api.get("/usage?days=90", headers=OWNER_H).json()
    assert body["total_calls"] == 4 and body["users"][0]["role"] == "staff"


def test_report_with_no_usage_lists_every_endpoint_as_unused(api):
    body = api.get("/usage", headers=OWNER_H).json()
    assert body["total_calls"] == 0 and body["routes"] == [] and body["users"] == []
    assert {"method": "GET", "route": "/usage"} not in body["unused"]
    assert {"method": "GET", "route": "/backups"} in body["unused"]


def test_report_survives_the_group_lookup_being_down(api, db, monkeypatch):
    # _groups returns None when Google is unreachable and there's no snapshot;
    # everyone then reads as a cadet rather than the page failing.
    seed(db, [(date.today(), "GET", "/reference", PERSONAS["staff"]["email"], 1)])
    from core import security
    monkeypatch.setattr(security, "_groups", lambda unknown=False: None)
    # require_owner checks the email, not a group, so the owner still gets in.
    body = api.get("/usage", headers=OWNER_H).json()
    assert body["users"][0]["role"] == "cadet"


# ── retention ─────────────────────────────────────────────────────────────────

def test_cleanup_drops_counters_past_retention_only(db):
    today = date.today()
    keep = today - timedelta(days=usage_router.USAGE_RETENTION_DAYS - 1)
    seed(db, [
        (today - timedelta(days=usage_router.USAGE_RETENTION_DAYS + 1), "GET", "/stats/current", OWNER, 1),
        (keep, "GET", "/reference", OWNER, 1),
    ])
    usage_router.cleanup_old_usage()
    db.expire_all()
    assert [r.day for r in db.query(UsageDaily).all()] == [keep]
