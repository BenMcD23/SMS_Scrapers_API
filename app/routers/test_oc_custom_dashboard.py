"""The OC's custom dashboard: the query builder behind its panels, and saving
the panels themselves."""

import os
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core import dashboard_query
from core.attendance import attendance_state
from database.models import (
    Base,
    Cadet,
    CadetAttendance,
    CadetFlight,
    CadetQualification,
    DashboardLayout,
    Staff,
    StaffAttendance,
)

OC = {"Authorization": "Bearer oc"}
QUERY = "/oc/custom-dashboard/query"
DASH = "/oc/custom-dashboard"
DATASETS = "/oc/custom-dashboard/datasets"


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="One", flight="A", rank="Cadet",
              classification="Leading Cadet", email="amy@example.com"),
        Cadet(cin=2, first_name="Ben", last_name="Two", flight="A", rank="Corporal",
              classification="Junior Cadet"),
        Cadet(cin=3, first_name="Zoë", last_name="Three", flight="B", rank="Cadet"),
        Cadet(cin=4, first_name="Dan", last_name="Four", flight=None, rank="Cadet"),
    ])
    nights = [datetime(2026, 9, 3, 19), datetime(2026, 9, 10, 19), datetime(2026, 10, 1, 19)]
    statuses = {
        1: ["Present Correctly Dressed", "Present Incorrectly Dressed", "Absent"],
        2: ["Authorised Absence", "Present Correctly Dressed", "Present Correctly Dressed"],
        3: ["Absent", "Absent", "Present Correctly Dressed"],
    }
    for cin, states in statuses.items():
        for night, status in zip(nights, states, strict=True):
            db.add(CadetAttendance(cadet_id=cin, date=night, register_type="Parade Night", status=status))
    db.add(Staff(cin=10, first_name="Sue", last_name="Staff", rank="Sgt"))
    db.add(StaffAttendance(staff_id=10, date=nights[0], register_type="Parade Night", status="Present"))
    db.add_all([
        CadetQualification(cadet_id=1, qual_type="first_aid", status="blue",
                           date_achieved=datetime(2026, 1, 5), date_expires=datetime(2029, 1, 5)),
        CadetQualification(cadet_id=2, qual_type="first_aid", status="bronze",
                           date_achieved=datetime(2025, 6, 1)),
        CadetQualification(cadet_id=1, qual_type="radio", status="blue",
                           date_achieved=datetime(2026, 3, 1)),
    ])
    db.add_all([
        CadetFlight(cadet_id=1, date=date(2026, 8, 1), activity="powered", minutes=30),
        CadetFlight(cadet_id=1, date=date(2026, 8, 20), activity="gliding", minutes=15),
        CadetFlight(cadet_id=3, date=date(2026, 9, 2), activity="powered", minutes=None),
    ])
    db.commit()


def query(api, range_days=None, **spec):
    return api.post(QUERY, json={"query": spec, "range_days": range_days}, headers=OC)


def rows(res):
    assert res.status_code == 200, res.text
    return [(tuple(r["keys"]), r["value"]) for r in res.json()["rows"]]


# ── Access ────────────────────────────────────────────────────────────────────

ENDPOINTS = [("get", DATASETS, None), ("post", QUERY, {"query": {"dataset": "cadets"}}),
             ("get", DASH, None), ("put", DASH, {"panels": []})]


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_custom_dashboard_needs_a_token(api, method, path, body):
    kw = {"json": body} if body else {}
    assert getattr(api, method)(path, **kw).status_code == 401


@pytest.mark.parametrize("persona", ["staff", "owner", "snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_custom_dashboard_is_oc_only(api, persona, method, path, body):
    kw = {"json": body} if body else {}
    assert getattr(api, method)(path, headers=api.as_(persona), **kw).status_code == 403


# ── Catalogue ─────────────────────────────────────────────────────────────────

def test_datasets_list_what_a_panel_can_use(api):
    res = api.get(DATASETS, headers=OC)
    assert res.status_code == 200
    by_key = {d["key"]: d for d in res.json()}
    assert set(by_key) == {"cadets", "cadet_attendance", "staff_attendance", "qualifications", "flying"}
    attendance = by_key["cadet_attendance"]
    assert attendance["time_field"] == "date"
    assert {"key": "turnout", "label": "Turnout", "unit": "%"} in attendance["measures"]
    assert {"key": "date", "label": "Date", "kind": "date"} in attendance["fields"]


def test_no_dataset_offers_personal_data(api):
    # The allowlist is the privacy boundary: names, contact details, dates of
    # birth and medical data must never be groupable or filterable.
    personal = {"first_name", "last_name", "email", "phone_number", "parent_emails",
                "date_of_birth", "address", "cin", "cadet_id", "staff_id"}
    for ds in api.get(DATASETS, headers=OC).json():
        assert not personal & {f["key"] for f in ds["fields"]}, ds["key"]


# ── Querying ──────────────────────────────────────────────────────────────────

def test_a_count_with_no_grouping_is_one_number(api, squadron):
    assert rows(query(api, dataset="cadets")) == [((), 4)]


def test_cadets_grouped_by_flight_include_the_ones_without_one(api, squadron):
    assert rows(query(api, dataset="cadets", group_by=[{"field": "flight"}])) == [
        ((None,), 1), (("A",), 2), (("B",), 1)]


def test_sorting_by_value_puts_the_biggest_first(api, squadron):
    res = query(api, dataset="cadets", group_by=[{"field": "rank"}], sort="value_desc")
    assert rows(res) == [(("Cadet",), 3), (("Corporal",), 1)]


def test_turnout_is_present_over_everyone_on_the_register(api, squadron):
    res = query(api, dataset="cadet_attendance", measure="turnout",
                group_by=[{"field": "date", "grain": "month"}])
    # September: 3 present of 6 (an authorised absence isn't present); October: 2 of 3.
    assert rows(res) == [(("2026-09-01",), 50), (("2026-10-01",), 66.67)]
    assert res.json()["unit"] == "%"


@pytest.mark.parametrize("grain,expected", [
    ("day", ["2026-09-03", "2026-09-10", "2026-10-01"]),
    # Weeks start on Monday: 3 Sep 2026 is a Thursday.
    ("week", ["2026-08-31", "2026-09-07", "2026-09-28"]),
    ("month", ["2026-09-01", "2026-10-01"]),
    ("year", ["2026-01-01"]),
])
def test_dates_bucket_to_the_first_day_of_each_period(api, squadron, grain, expected):
    res = query(api, dataset="cadet_attendance", group_by=[{"field": "date", "grain": grain}])
    assert [k[0] for k, _ in rows(res)] == expected


def test_two_groupings_split_each_bar(api, squadron):
    res = query(api, dataset="cadet_attendance", group_by=[{"field": "flight"}, {"field": "state"}],
                filters=[{"field": "flight", "op": "not_empty"}])
    assert rows(res) == [
        (("A", "absent"), 1), (("A", "authorised"), 1), (("A", "present"), 4),
        (("B", "absent"), 2), (("B", "present"), 1)]


@pytest.mark.parametrize("status", [
    "Present Correctly Dressed", "Present Incorrectly Dressed", "Present", "Authorised Absence",
    "authorized absence", "Absent", "Unauthorised Absence", "", None, "Late"])
def test_sql_attendance_state_agrees_with_the_python_rule(db, status):
    # Everything else (registers, HTD claims) uses core.attendance; a panel
    # that disagreed would show a different turnout to the rest of the site.
    db.add(Cadet(cin=1, first_name="A", last_name="One"))
    db.add(CadetAttendance(cadet_id=1, date=datetime(2026, 9, 3), status=status))
    db.commit()
    spec = dashboard_query.QuerySpec(dataset="cadet_attendance", group_by=[{"field": "state"}])
    [row] = dashboard_query.run(db, spec)["rows"]
    assert row["keys"] == [attendance_state(status)]


@pytest.mark.parametrize("flt,expected", [
    ({"field": "flight", "op": "eq", "value": "A"}, 2),
    # "Not A" keeps the cadets with no flight: NULL <> 'A' alone would drop them.
    ({"field": "flight", "op": "neq", "value": "A"}, 2),
    ({"field": "flight", "op": "in", "value": ["A", "B"]}, 3),
    ({"field": "flight", "op": "not_in", "value": ["A"]}, 2),
    ({"field": "flight", "op": "empty"}, 1),
    ({"field": "flight", "op": "not_empty"}, 3),
    ({"field": "classification", "op": "empty"}, 2),
])
def test_filters_narrow_the_rows(api, squadron, flt, expected):
    assert rows(query(api, dataset="cadets", filters=[flt])) == [((), expected)]


def test_date_filters_compare_as_dates(api, squadron):
    res = query(api, dataset="qualifications",
                filters=[{"field": "date_achieved", "op": "gte", "value": "2026-01-01"}])
    assert rows(res) == [((), 2)]


@pytest.mark.parametrize("range_days,status", [(1, 200), (3660, 200), (0, 422), (3661, 422)])
def test_range_days_is_bounded(api, range_days, status):
    assert query(api, range_days, dataset="cadet_attendance").status_code == status


def test_range_days_counts_back_from_today(db, squadron):
    # Called directly with a pinned "today" so the window doesn't drift with the calendar.
    spec = dashboard_query.QuerySpec(dataset="cadet_attendance")
    # 25 days before 26 Oct is 1 Oct: only that night is in.
    assert dashboard_query.run(db, spec, 25, today=date(2026, 10, 26))["rows"] == [{"keys": [], "value": 3}]
    assert dashboard_query.run(db, spec, None, today=date(2026, 10, 26))["rows"] == [{"keys": [], "value": 9}]


def test_range_days_leaves_the_roster_alone(db, squadron):
    # The roster is today's snapshot; there's no date to narrow.
    spec = dashboard_query.QuerySpec(dataset="cadets")
    assert dashboard_query.run(db, spec, 1, today=date(2030, 1, 1))["rows"] == [{"keys": [], "value": 4}]


def test_flying_minutes_add_up_and_skip_unknown_lengths(api, squadron):
    assert rows(query(api, dataset="flying", measure="minutes")) == [((), 45)]
    assert rows(query(api, dataset="flying", measure="avg_minutes")) == [((), 22.5)]
    assert rows(query(api, dataset="flying", measure="people")) == [((), 2)]


def test_number_filters_need_a_number(api, squadron):
    ok = query(api, dataset="flying", filters=[{"field": "minutes", "op": "gte", "value": 20}])
    assert rows(ok) == [((), 1)]
    bad = query(api, dataset="flying", filters=[{"field": "minutes", "op": "gte", "value": "lots"}])
    assert bad.status_code == 400 and "isn't a number" in bad.json()["detail"]


def test_an_empty_dataset_answers_zero_or_no_rows(api):
    assert rows(query(api, dataset="cadets")) == [((), 0)]
    assert rows(query(api, dataset="cadets", group_by=[{"field": "flight"}])) == []
    # avg over nothing is no answer, not zero.
    assert rows(query(api, dataset="cadet_attendance", measure="turnout")) == [((), None)]


def test_a_long_answer_is_cut_and_says_so(api, squadron):
    res = query(api, dataset="cadets", group_by=[{"field": "flight"}], limit=2)
    assert len(res.json()["rows"]) == 2 and res.json()["truncated"] is True
    assert query(api, dataset="cadets", group_by=[{"field": "flight"}]).json()["truncated"] is False


@pytest.mark.parametrize("spec,message", [
    ({"dataset": "users"}, "Unknown dataset"),
    ({"dataset": "cadets", "measure": "turnout"}, "no measure 'turnout'"),
    ({"dataset": "cadets", "group_by": [{"field": "email"}]}, "can't be grouped by 'email'"),
    ({"dataset": "cadets", "group_by": [{"field": "flight"}, {"field": "flight"}]}, "only once"),
    ({"dataset": "cadets", "filters": [{"field": "date_of_birth", "op": "empty"}]},
     "can't be filtered by 'date_of_birth'"),
    ({"dataset": "cadets", "filters": [{"field": "flight", "op": "eq"}]}, "needs a single value"),
    ({"dataset": "cadets", "filters": [{"field": "flight", "op": "in", "value": []}]}, "at least one"),
    ({"dataset": "cadets", "filters": [{"field": "flight", "op": "gt", "value": "A"}]}, "can only be matched"),
    ({"dataset": "cadets", "filters": [{"field": "joined_on", "op": "gt", "value": "last week"}]},
     "isn't a date"),
])
def test_anything_off_the_allowlist_is_refused(api, squadron, spec, message):
    res = query(api, **spec)
    assert res.status_code == 400 and message in res.json()["detail"]


@pytest.mark.parametrize("spec", [
    {"dataset": "cadets", "group_by": [{"field": "flight"}] * 3},
    {"dataset": "cadets", "limit": 501},
    {"dataset": "cadets", "limit": 0},
    {"dataset": "cadets", "filters": [{"field": "flight", "op": "like", "value": "A"}]},
    {"dataset": "cadets", "sort": "random()"},
])
def test_malformed_specs_are_rejected_before_they_reach_sql(api, spec):
    assert query(api, **spec).status_code == 422


def test_filter_values_are_bound_not_spliced_into_sql(api, squadron, db):
    evil = "A'; DROP TABLE \"Cadets\"; --"
    assert rows(query(api, dataset="cadets", filters=[{"field": "flight", "op": "eq", "value": evil}])) == [((), 0)]
    assert db.query(Cadet).count() == 4


# ── Saving the dashboard ──────────────────────────────────────────────────────

def panel(**kw):
    return {"id": "p1", "title": "Cadets by flight", "viz": "bar", "x": 0, "y": 0, "w": 6, "h": 4,
            "query": {"dataset": "cadets", "group_by": [{"field": "flight"}]}, **kw}


def test_a_new_oc_starts_with_an_empty_dashboard(api):
    assert api.get(DASH, headers=OC).json() == {"range_days": 90, "panels": [], "customised": False}


def test_saved_panels_come_back(api):
    body = {"range_days": 30, "panels": [panel(), panel(id="p2", viz="stat", x=6, w=3, h=2,
                                                        query={"dataset": "cadets"})]}
    saved = api.put(DASH, json=body, headers=OC)
    assert saved.status_code == 200
    got = api.get(DASH, headers=OC).json()
    assert got["customised"] is True and got["range_days"] == 30
    assert [p["id"] for p in got["panels"]] == ["p1", "p2"]
    assert got["panels"][1]["viz"] == "stat" and got["panels"][1]["x"] == 6
    assert saved.json() == got


def test_saving_again_replaces_rather_than_adds(api, db):
    api.put(DASH, json={"panels": [panel()]}, headers=OC)
    api.put(DASH, json={"panels": []}, headers=OC)
    assert api.get(DASH, headers=OC).json()["panels"] == []
    assert db.query(DashboardLayout).filter_by(dashboard="oc-custom").count() == 1


def test_the_custom_dashboard_doesnt_touch_the_section_order(api):
    api.put("/oc/dashboard/layout", json={"sections": ["pipeline"]}, headers=OC)
    api.put(DASH, json={"panels": [panel()]}, headers=OC)
    assert api.get("/oc/dashboard/layout", headers=OC).json()["sections"][0] == "pipeline"
    assert len(api.get(DASH, headers=OC).json()["panels"]) == 1


@pytest.mark.parametrize("panels,message", [
    ([panel(), panel()], "its own id"),
    ([panel(x=8, w=6)], "runs off the side"),
    ([panel(query={"dataset": "cadets", "group_by": [{"field": "email"}]})], "Cadets by flight: "),
])
def test_bad_panels_are_refused_and_nothing_is_saved(api, panels, message):
    res = api.put(DASH, json={"panels": panels}, headers=OC)
    assert res.status_code == 400 and message in res.json()["detail"]
    assert api.get(DASH, headers=OC).json()["customised"] is False


@pytest.mark.parametrize("bad", [
    {"viz": "pie"}, {"title": ""}, {"id": "has space"}, {"w": 0}, {"h": 21}, {"x": -1}])
def test_malformed_panels_are_422(api, bad):
    assert api.put(DASH, json={"panels": [panel(**bad)]}, headers=OC).status_code == 422


def test_too_many_panels_are_refused(api):
    panels = [panel(id=f"p{i}", y=i) for i in range(41)]
    assert api.put(DASH, json={"panels": panels}, headers=OC).status_code == 422


@pytest.mark.parametrize("stored", ["not json", "[1, 2]", '{"panels": "nope"}'])
def test_a_damaged_row_reads_as_an_empty_dashboard(api, db, stored):
    api.put(DASH, json={"panels": [panel()]}, headers=OC)
    row = db.query(DashboardLayout).filter_by(dashboard="oc-custom").one()
    row.sections = stored
    db.commit()
    res = api.get(DASH, headers=OC)
    assert res.status_code == 200 and res.json()["panels"] == []


# ── Postgres ──────────────────────────────────────────────────────────────────
#
# Production runs Postgres, where date bucketing is different SQL and GROUP BY
# is stricter than SQLite's. Skips unless TEST_POSTGRES_URL is set, e.g.
#   TEST_POSTGRES_URL=postgresql://app:<password>@localhost:5433/app pytest -k postgres

PG_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.skipif(not PG_URL, reason="needs TEST_POSTGRES_URL (a real Postgres)")
def test_every_dataset_measure_and_grouping_runs_on_postgres():
    schema = "dashboard_query_test"
    admin = create_engine(PG_URL)
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema}"))
    # Its own schema, so the test never touches the tables already there.
    engine = create_engine(PG_URL, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(Cadet(cin=1, first_name="A", last_name="One", flight="A", joined_on=date(2025, 9, 1)))
            db.add(CadetAttendance(cadet_id=1, date=datetime(2026, 9, 3, 19), status="Present"))
            db.add(CadetFlight(cadet_id=1, date=date(2026, 8, 1), activity="powered", minutes=30))
            db.commit()
            for key, ds in dashboard_query.DATASETS.items():
                for measure in ds.measures:
                    for name, fd in ds.fields.items():
                        for grain in (["day", "week", "month", "year"] if fd.kind == "date" else ["month"]):
                            other = next(k for k in ds.fields if k != name)
                            spec = dashboard_query.QuerySpec(
                                dataset=key, measure=measure, sort="value_desc",
                                group_by=[{"field": name, "grain": grain}, {"field": other}],
                                filters=[{"field": name, "op": "not_empty"}])
                            dashboard_query.run(db, spec, 3650)
                            db.rollback()
            spec = dashboard_query.QuerySpec(dataset="cadet_attendance", measure="turnout",
                                             group_by=[{"field": "date", "grain": "week"}])
            assert dashboard_query.run(db, spec)["rows"] == [{"keys": ["2026-08-31"], "value": 100}]
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()
