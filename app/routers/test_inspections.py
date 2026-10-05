"""Inspection sheets: submission/merging, AWOL detection, scoring, history
rankings, per-date browsing, PDF export and the AI analysis."""

from datetime import datetime

import pytest

import routers.inspections as insp
from database.models import Cadet, CadetAbsence, InspectionSheet

STAFF = {"Authorization": "Bearer staff"}
SNCO = {"Authorization": "Bearer snco"}


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="Able", flight="A", rank="Cpl"),
        Cadet(cin=2, first_name="Ben", last_name="Baker", flight="A"),
        Cadet(cin=3, first_name="Cal", last_name="Cole", flight="A"),
        Cadet(cin=4, first_name="Dee", last_name="Dunn", flight="B"),
        Cadet(cin=5, first_name="Eve", last_name="Ent", flight="A", banned=True),
    ])
    # Ben has a logged absence for the parade night.
    db.add(CadetAbsence(cadet_id=2, date_from=datetime(2026, 3, 1), date_to=datetime(2026, 3, 10),
                        reason="Holiday", scraped_at=datetime(2026, 3, 1)))
    db.commit()


def _submit(api, marks, date="2026-03-04", headers=STAFF, **extra):
    return api.post("/inspections", json={"date": date, "marks": marks, **extra}, headers=headers)


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/absences"), ("post", "/inspections"), ("get", "/inspections/history"),
    ("get", "/inspections/sheets"), ("get", "/inspections/sheets/1"), ("delete", "/inspections/sheets/1"),
    ("get", "/inspections/sheets/1/pdf"), ("post", "/inspections/analyse"),
])
def test_staff_and_snco_only(api, method, path):
    def call(headers):
        if method in ("get", "delete"):
            return getattr(api, method)(path, headers=headers)
        return api.post(path, json={}, headers=headers)

    assert call({}).status_code == 401
    for persona in ("nco", "cadet"):
        assert call(api.as_(persona)).status_code == 403
    for persona in ("staff", "snco"):
        assert call(api.as_(persona)).status_code not in (401, 403)


# ── helpers ───────────────────────────────────────────────────────────────────

def test_parse_date_rejects_bad_input(api):
    assert api.get("/absences?date=04/03/2026", headers=STAFF).status_code == 400
    assert _submit(api, [], date="yesterday").status_code == 400


@pytest.mark.parametrize("n,label", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"),
    (19, "19th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (101, "101st"), (111, "111st"),
])
def test_ordinal_mirrors_the_spreadsheet(n, label):
    # 111 → "111st": the spreadsheet only special-cases 11–19, and we match it.
    assert insp._ordinal(n) == label


@pytest.mark.parametrize("values,ranks", [
    ([], []),
    ([5], [1]),
    ([1, 3, 2], [3, 1, 2]),
    ([9, 9, 7], [1, 1, 3]),
    ([0, 0, 0], [1, 1, 1]),
])
def test_competition_ranks(values, ranks):
    assert insp._competition_ranks(values) == ranks


def test_flight_scores_unit():
    marks = [
        {"cin": 1, "score": 7.5, "absent": False},
        {"cin": 2, "absent": True},           # excused
        {"cin": 3, "absent": True},           # awol
        {"cin": 4, "score": None},            # present, unscored
        {"cin": 9, "score": 3},               # cadet not on the roster
    ]
    scores = insp._flight_scores(marks, {3}, {1: "A", 2: "A", 3: "A", 4: "B"})
    assert scores["A"] == {"total": 2.5, "present_count": 1, "awol_count": 1}
    assert scores["B"] == {"total": 0.0, "present_count": 1, "awol_count": 0}
    assert scores["Unassigned"]["total"] == 3


def test_summarise_timeline_empty_and_mixed():
    assert insp.summarise_timeline([]) == {
        "present_count": 0, "attendance_avg": 0.0, "score_avg": 0.0, "overall": 0.0}
    tl = [{"absent": False, "score": 8}, {"absent": False, "score": None}, {"absent": True, "score": None},
          {"absent": False, "score": 6}]
    assert insp.summarise_timeline(tl) == {
        "present_count": 3, "attendance_avg": 75.0, "score_avg": 7.0, "overall": 5.25}


# ── absences ──────────────────────────────────────────────────────────────────

def test_absences_on_a_date_inclusive(api, squadron):
    assert [a["cin"] for a in api.get("/absences?date=2026-03-01", headers=SNCO).json()] == [2]
    assert [a["cin"] for a in api.get("/absences?date=2026-03-10", headers=SNCO).json()] == [2]
    assert api.get("/absences?date=2026-03-11", headers=SNCO).json() == []
    # No date means today — just mustn't error.
    assert api.get("/absences", headers=SNCO).status_code == 200


# ── submit ────────────────────────────────────────────────────────────────────

def test_submit_resolves_the_whole_flight(api, db, squadron):
    res = _submit(api, [
        {"cin": 1, "score": 8, "comments": [{"type": "fault", "region": "Shoes", "text": "dull"}]},
        {"cin": 3, "absent": True},
    ], uniform="mtp")
    assert res.status_code == 200
    body = res.json()
    # Cal marked absent with no log = AWOL; Ben (not marked, has a log) is
    # excused; banned Eve and B-flight Dee aren't pulled in.
    assert body["awol"] == [3]
    assert body["flight_scores"] == {"A": {"total": 3.0, "present_count": 1, "awol_count": 1}}

    sheet = db.get(InspectionSheet, body["id"])
    marks = {m["cin"]: m for m in sheet.data["marks"]}
    assert set(marks) == {1, 2, 3}
    assert marks[2] == {**marks[2], "absent": True, "awol": False, "auto": True}
    assert sheet.data["uniform"] == "mtp"
    assert sheet.submitted_by == "staff@317atc.co.uk"


def test_unmarked_cadet_without_a_log_is_awol(api, squadron):
    body = _submit(api, [{"cin": 1, "score": 5}], date="2026-04-01").json()
    # Ben's absence doesn't cover April, so both Ben and Cal are AWOL.
    assert body["awol"] == [2, 3]
    assert body["flight_scores"]["A"]["total"] == 5 - 2 * insp.AWOL_PENALTY


def test_second_submitter_merges_and_wins_on_overlap(api, db, squadron):
    first = _submit(api, [{"cin": 1, "score": 4}, {"cin": 3, "score": 6}]).json()
    second = _submit(api, [{"cin": 1, "score": 9}, {"cin": 4, "score": 7}], headers=SNCO).json()
    assert first["id"] == second["id"]
    assert db.query(InspectionSheet).count() == 1

    sheet = db.get(InspectionSheet, first["id"])
    db.refresh(sheet)
    marks = {m["cin"]: m for m in sheet.data["marks"]}
    assert marks[1]["score"] == 9 and marks[3]["score"] == 6 and marks[4]["score"] == 7
    assert sheet.submitted_by == "staff@317atc.co.uk, snco@317atc.co.uk"
    # Only the flights this submitter touched are reported back.
    assert second["awol"] == []
    assert set(second["flight_scores"]) == {"A", "B"}

    # The first inspector submitting again moves to the end, not duplicated.
    _submit(api, [{"cin": 3, "score": 6}])
    db.refresh(sheet)
    assert sheet.submitted_by == "snco@317atc.co.uk, staff@317atc.co.uk"


def test_a_later_real_mark_replaces_an_auto_awol(api, db, squadron):
    _submit(api, [{"cin": 1, "score": 5}], date="2026-04-01")
    body = _submit(api, [{"cin": 3, "score": 8}], date="2026-04-01").json()
    sheet = db.get(InspectionSheet, body["id"])
    db.refresh(sheet)
    marks = {m["cin"]: m for m in sheet.data["marks"]}
    assert marks[3]["awol"] is False and "auto" not in marks[3]
    assert body["awol"] == [2]


def test_submit_for_unknown_cadets_does_not_crash(api):
    body = _submit(api, [{"cin": 999, "score": 3}]).json()
    assert body["flight_scores"] == {"Unassigned": {"total": 3.0, "present_count": 1, "awol_count": 0}}


@pytest.mark.parametrize("bad", [
    {"date": "2026-03-04"},
    {"date": "2026-03-04", "marks": [{"score": 3}]},
    {"date": "2026-03-04", "marks": [{"cin": "abc"}]},
    {"marks": []},
])
def test_submit_validation(api, bad):
    assert api.post("/inspections", json=bad, headers=STAFF).status_code == 422


# ── history ───────────────────────────────────────────────────────────────────

def test_history_ranks_and_averages(api, squadron):
    _submit(api, [{"cin": 1, "score": 8}, {"cin": 3, "score": 6}, {"cin": 4, "score": 8}], date="2026-03-04")
    _submit(api, [{"cin": 1, "score": 6}, {"cin": 3, "absent": True}, {"cin": 4, "score": 8}], date="2026-03-11")

    body = api.get("/inspections/history", headers=STAFF).json()
    assert body["inspection_count"] == 2 and body["dates"] == ["2026-03-04", "2026-03-11"]
    rows = {r["cin"]: r for r in body["cadets"]}
    assert 5 not in rows  # banned cadets are left out

    assert (rows[1]["attendance_avg"], rows[1]["score_avg"], rows[1]["overall"]) == (100.0, 7.0, 7.0)
    assert (rows[4]["attendance_avg"], rows[4]["score_avg"], rows[4]["overall"]) == (100.0, 8.0, 8.0)
    assert (rows[3]["attendance_avg"], rows[3]["score_avg"]) == (50.0, 6.0)
    assert rows[3]["timeline"][1]["awol"] is True

    assert rows[4]["overall_rank"] == 1 and rows[4]["overall_rank_label"] == "1st"
    assert rows[1]["overall_rank"] == 2
    # Amy and Dee both attended every night: shared attendance rank.
    assert rows[1]["attendance_rank"] == rows[4]["attendance_rank"] == 1


def test_history_with_no_sheets(api, squadron):
    body = api.get("/inspections/history", headers=STAFF).json()
    assert body["inspection_count"] == 0
    assert all(r["timeline"] == [] and r["overall_rank"] == 1 for r in body["cadets"])


def test_history_tolerates_legacy_sheets(api, db, squadron):
    db.add(InspectionSheet(date=datetime(2025, 1, 1), submitted_at=datetime(2025, 1, 1),
                           data={"marks": [{"score": 3}, {"cin": 1, "score": 4}]}))
    db.add(InspectionSheet(date=datetime(2025, 1, 2), submitted_at=datetime(2025, 1, 2), data={}))
    db.commit()
    rows = {r["cin"]: r for r in api.get("/inspections/history", headers=STAFF).json()["cadets"]}
    assert rows[1]["timeline"][0]["uniform"] == "blues"


# ── sheets ────────────────────────────────────────────────────────────────────

def test_sheets_list_detail_pdf_delete(api, squadron):
    sid = _submit(api, [
        {"cin": 1, "score": 8, "comments": [{"type": "fault", "region": "Shoes", "text": "dull"},
                                            {"type": "positive", "region": "Beret", "text": "sharp"}]},
        {"cin": 3, "absent": True},
        {"cin": 4, "score": 6},
    ]).json()["id"]

    listing = api.get("/inspections/sheets", headers=STAFF).json()
    assert listing == [{**listing[0], "id": sid, "date": "2026-03-04", "cadet_count": 4, "present": 2,
                        "uniform": "blues"}]

    detail = api.get(f"/inspections/sheets/{sid}", headers=STAFF).json()
    flights = {f["flight"]: f for f in detail["flights"]}
    assert [f["flight"] for f in detail["flights"]] == ["A", "B"]
    a = flights["A"]
    assert (a["present"], a["awol"], a["penalty"], a["average"], a["total"]) == (1, 1, 5, 8.0, 3.0)
    assert [c["last_name"] for c in a["cadets"]] == ["Able", "Baker", "Cole"]
    amy = a["cadets"][0]
    assert amy["faults"] == [{"region": "Shoes", "text": "dull"}]
    assert amy["positives"] == [{"region": "Beret", "text": "sharp"}]

    pdf = api.get(f"/inspections/sheets/{sid}/pdf", headers=STAFF)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "inspection-2026-03-04.pdf" in pdf.headers["content-disposition"]

    assert api.delete(f"/inspections/sheets/{sid}", headers=STAFF).json() == {"deleted": sid}
    for method in ("get",):
        assert getattr(api, method)(f"/inspections/sheets/{sid}", headers=STAFF).status_code == 404
    assert api.get(f"/inspections/sheets/{sid}/pdf", headers=STAFF).status_code == 404
    assert api.delete(f"/inspections/sheets/{sid}", headers=STAFF).status_code == 404


def test_mtp_pdf_and_unknown_cadet_in_detail(api, db):
    db.add(InspectionSheet(date=datetime(2026, 1, 1), submitted_at=datetime(2026, 1, 1),
                           data={"uniform": "mtp", "marks": [{"cin": 77, "score": 2}]}))
    db.commit()
    detail = api.get("/inspections/sheets/1", headers=STAFF).json()
    cadet = detail["flights"][0]["cadets"][0]
    assert detail["flights"][0]["flight"] == "Unassigned"
    assert (cadet["first_name"], cadet["last_name"]) == ("Unknown", "77")
    assert api.get("/inspections/sheets/1/pdf", headers=STAFF).content.startswith(b"%PDF")


def test_flight_order_puts_ncos_first_and_unknowns_last():
    flights = ["Unassigned", "C", "NCO", "A", "Zulu", "B"]
    assert sorted(flights, key=insp._flight_order) == ["NCO", "A", "B", "C", "Unassigned", "Zulu"]


# ── AI analysis ───────────────────────────────────────────────────────────────

def test_analyse_builds_a_prompt_from_recurring_faults(api, squadron, monkeypatch):
    seen = {}

    def fake_generate(prompt, system, **kw):
        seen["prompt"] = prompt
        return "Looks good", "fake-model"

    monkeypatch.setattr(insp, "generate", fake_generate)
    fault = {"type": "fault", "region": "Shoes", "text": " Dull "}
    _submit(api, [{"cin": 1, "score": 6, "comments": [fault]}], date="2026-03-04")
    _submit(api, [{"cin": 1, "score": None, "comments": [{**fault, "text": "dull"}]}], date="2026-03-11")
    _submit(api, [{"cin": 1, "absent": True}], date="2026-03-18")

    res = api.post("/inspections/analyse", json={"cin": 1}, headers=STAFF).json()
    assert res == {"cin": 1, "model": "fake-model", "analysis": "Looks good"}
    prompt = seen["prompt"]
    assert "Cpl Amy Able" in prompt
    assert "6.0, ?, -" in prompt or "6, ?, -" in prompt
    # Faults are normalised so the same note counts as recurring.
    assert '"dull" (×2)' in prompt
    assert "Positive notes logged:\n- None logged" in prompt


def test_analyse_errors(api, squadron, monkeypatch):
    assert api.post("/inspections/analyse", json={"cin": 99}, headers=STAFF).status_code == 404
    res = api.post("/inspections/analyse", json={"cin": 1}, headers=STAFF)
    assert res.status_code == 404 and "No inspection history" in res.json()["detail"]

    def down(*a, **k):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(insp, "generate", down)
    _submit(api, [{"cin": 1, "score": 5}])
    res = api.post("/inspections/analyse", json={"cin": 1}, headers=STAFF)
    assert res.status_code == 503 and "providers down" in res.json()["detail"]
