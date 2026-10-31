"""The /programme endpoints over HTTP: who can import and read, the doc
failing every way, the backfill, and the stats. The parsing and tidying are
tested in texts/ and core/; here the doc is a fake that returns parsed nights.
"""

from datetime import date, datetime

import pytest

import core.programme as pg
import texts.programme_parser as pp
from database.models import Cadet, CadetAttendance, ProgrammeNight


def nights_for(month, year):
    return [{"date": datetime(year, month, 7), "uniform": "No.3 SD", "dnco": "Cpl Jones", "sessions": [
        {"period": 1, "flights": ["A", "B"], "text": "Drill\nSgt Smith", "split": False},
        {"period": 2, "flights": ["C"], "text": "History of the RAF\nFS Gray", "split": False},
    ]}]


@pytest.fixture
def doc(monkeypatch):
    """The programme doc: parsed nights per month, or an exception to raise.
    The model is down throughout, so the rules tidy (the model has its tests)."""
    state = {"fail": {}, "asked": []}

    def fake_parse(month, year):
        state["asked"].append((month, year))
        if (month, year) in state["fail"]:
            raise state["fail"][(month, year)]
        return nights_for(month, year)

    def down(*_a, **_kw):
        raise RuntimeError("down")

    monkeypatch.setattr(pp, "parse_programme_sessions", fake_parse)
    monkeypatch.setattr(pg, "generate", down)
    return state


# ─── Who can do what ─────────────────────────────────────────────────────────

ENDPOINTS = [
    ("get", "/programme/months", None),
    ("post", "/programme/import", {"month": 10, "year": 2026}),
    ("post", "/programme/backfill", {"start": "2026-09"}),
    ("get", "/programme/nights?start=2026-10-01&end=2026-10-31", None),
    ("get", "/programme/stats", None),
    ("get", "/programme/cadets/1", None),
]


@pytest.mark.parametrize("method, path, body", ENDPOINTS)
def test_no_token_is_401(api, method, path, body):
    kw = {"json": body} if body else {}
    assert getattr(api, method)(path, **kw).status_code == 401


@pytest.mark.parametrize("persona, allowed", [
    ("cadet", set()),
    ("nco", {"/programme/nights", "/programme/stats"}),
    ("snco", {"/programme/nights", "/programme/stats"}),
    ("staff", {"/programme/months", "/programme/import", "/programme/nights", "/programme/stats",
               "/programme/cadets/1"}),
    ("owner", {p.split("?")[0] for _, p, _ in ENDPOINTS}),
])
def test_each_role_reaches_only_its_endpoints(api, db, doc, persona, allowed):
    db.add(Cadet(cin=1, first_name="Amy", last_name="A", flight="A"))
    db.commit()
    for method, path, body in ENDPOINTS:
        kw = {"json": body} if body else {}
        code = getattr(api, method)(path, headers=api.as_(persona), **kw).status_code
        if path.split("?")[0] in allowed:
            assert code in (200, 202), (persona, path, code)
        else:
            assert code == 403, (persona, path, code)


# ─── Importing ───────────────────────────────────────────────────────────────

def test_import_a_month_then_list_it(api, db, doc):
    res = api.post("/programme/import", json={"month": 10, "year": 2026}, headers=api.as_("staff"))
    assert res.status_code == 200
    assert res.json() == {"month": "2026-10", "nights": 1, "sessions": 3}
    [month] = api.get("/programme/months", headers=api.as_("staff")).json()
    assert (month["month"], month["nights"], month["sessions"]) == ("2026-10", 1, 3)


@pytest.mark.parametrize("error, status, detail", [
    (FileNotFoundError("No '10_26' programme doc in the 2026 folder"), 404, "No '10_26' programme doc"),
    (ValueError("Could not identify the programme table columns"), 422, "Could not identify"),
    (ConnectionError("Google said no"), 502, "Couldn't read the programme doc"),
])
def test_a_doc_that_cant_be_read_says_why_and_changes_nothing(api, db, doc, error, status, detail):
    db.add(ProgrammeNight(date=date(2026, 10, 2), imported_at=datetime.now()))
    db.commit()
    doc["fail"][(10, 2026)] = error
    res = api.post("/programme/import", json={"month": 10, "year": 2026}, headers=api.as_("staff"))
    assert res.status_code == status and detail in res.json()["detail"]
    assert [n.date for n in db.query(ProgrammeNight)] == [date(2026, 10, 2)]


@pytest.mark.parametrize("body", [{"month": 13, "year": 2026}, {"month": 0, "year": 2026},
                                  {"month": 10, "year": 2019}, {"month": 10}])
def test_a_bad_month_is_422(api, doc, body):
    assert api.post("/programme/import", json=body, headers=api.as_("staff")).status_code == 422
    assert doc["asked"] == []


def test_backfill_imports_each_month_and_skips_missing_ones(api, db, doc):
    doc["fail"][(11, 2025)] = FileNotFoundError("No '11_25' programme doc in the 2025 folder")
    res = api.post("/programme/backfill", json={"start": "2025-10", "end": "2026-01"}, headers=api.as_("owner"))
    assert res.status_code == 202 and res.json() == {"months": 4, "from": "2025-10", "to": "2026-01"}
    # TestClient runs background tasks before returning.
    assert doc["asked"] == [(10, 2025), (11, 2025), (12, 2025), (1, 2026)]
    months = [m["month"] for m in api.get("/programme/months", headers=api.as_("owner")).json()]
    assert months == ["2026-01", "2025-12", "2025-10"]


@pytest.mark.parametrize("body, detail", [
    ({"start": "2020-01"}, "start in January 2023"),
    ({"start": "2026-05", "end": "2026-01"}, "before the start"),
    ({"start": "2023-01", "end": "2027-12"}, "At most 48 months"),
    ({"start": "2026-13"}, "isn't a month"),
])
def test_a_bad_backfill_is_422(api, doc, body, detail):
    res = api.post("/programme/backfill", json=body, headers=api.as_("owner"))
    assert res.status_code == 422 and detail in str(res.json()["detail"])
    assert doc["asked"] == []


def test_backfill_shape_is_checked(api, doc):
    assert api.post("/programme/backfill", json={"start": "Oct 2025"}, headers=api.as_("owner")).status_code == 422


# ─── Reading ─────────────────────────────────────────────────────────────────

def test_nights_lists_what_ran_merged_across_flights(api, db, doc):
    api.post("/programme/import", json={"month": 10, "year": 2026}, headers=api.as_("staff"))
    [n] = api.get("/programme/nights?start=2026-10-01&end=2026-10-31", headers=api.as_("nco")).json()
    assert n["date"] == "2026-10-07" and n["uniform"] == "No.3 SD"
    assert n["sessions"] == [
        {"period": 1, "flights": "A, B", "activity": "Drill", "category": "drill"},
        {"period": 2, "flights": "C", "activity": "History of the RAF", "category": "lessons"},
    ]


def test_nights_range_is_capped(api):
    res = api.get("/programme/nights?start=2024-01-01&end=2026-01-01", headers=api.as_("staff"))
    assert res.status_code == 422


def test_stats_and_one_cadet(api, db, doc):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="A", flight="A"),
                Cadet(cin=2, first_name="Ben", last_name="B", flight="B")])
    db.add_all([CadetAttendance(cadet_id=1, date=datetime(2026, 10, 7), status="Present"),
                CadetAttendance(cadet_id=2, date=datetime(2026, 10, 7), status="Absent")])
    db.commit()
    api.post("/programme/import", json={"month": 10, "year": 2026}, headers=api.as_("staff"))

    stats = api.get("/programme/stats?days=1095", headers=api.as_("nco")).json()
    [drill] = stats["categories"]
    assert (drill["key"], drill["rate"]) == ("drill", 50.0)
    assert stats["categories_all"][0] == {"key": "drill", "label": "Drill and inspection"}

    amy = api.get("/programme/cadets/1?days=1095", headers=api.as_("staff")).json()
    assert amy["nights"] == [{"date": "2026-10-07", "state": "present", "activities": ["Drill"]}]
    assert api.get("/programme/cadets/999", headers=api.as_("staff")).status_code == 404


def test_stats_with_nothing_imported_is_empty_not_an_error(api):
    out = api.get("/programme/stats", headers=api.as_("staff")).json()
    assert out["nights"] == [] and out["categories"] == [] and out["activities"] == []
