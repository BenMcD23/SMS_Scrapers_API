"""Inspection audit: the figures (fault areas, persistence, trends, flights,
cadets to watch, markers), the AI write-up and its name masking, and access."""

from datetime import datetime

import pytest

import routers.inspection_audit as audit_mod
from database.models import Cadet, InspectionSheet

STAFF = {"Authorization": "Bearer staff"}

CADETS = {
    1: {"first_name": "Amy", "last_name": "Able", "rank": "Cpl", "flight": "A", "banned": False},
    2: {"first_name": "Ben", "last_name": "Baker", "rank": None, "flight": "A", "banned": False},
    3: {"first_name": "Cal", "last_name": "Cole", "rank": None, "flight": "B", "banned": False},
    4: {"first_name": "Zoë", "last_name": "Öztürk", "rank": None, "flight": "B", "banned": True},
}


def fault(region, text):
    return {"type": "fault", "region": region, "text": text}


def positive(region, text):
    return {"type": "positive", "region": region, "text": text}


def mark(cin, score=None, *comments, absent=False, awol=False):
    return {"cin": cin, "score": score, "absent": absent, "awol": awol, "comments": list(comments)}


def sheet(date, *marks, uniform="blues", by="a@x"):
    return {"id": 0, "date": date, "uniform": uniform, "submitted_by": by, "marks": list(marks)}


# ── figures ───────────────────────────────────────────────────────────────────

def test_a_fault_on_consecutive_nights_ranks_above_a_more_common_one_off():
    sheets = [
        sheet("2026-01-07", mark(1, 6, fault("Shoes", "Dull")), mark(2, 7, fault("Beret", "badge"))),
        sheet("2026-01-14", mark(1, 6, fault("Shoes", "not bulled")), mark(3, 7, fault("Beret", "shape"))),
    ]
    a = audit_mod.build_audit(sheets, CADETS)
    # Beret hit two cadets once each; Shoes hit one cadet twice running.
    assert [f["region"] for f in a["faults"]] == ["Shoes", "Beret"]
    shoes, beret = a["faults"]
    assert (shoes["persistent_cadets"], shoes["cadets"], shoes["occurrences"]) == (1, 1, 2)
    assert (beret["persistent_cadets"], beret["cadets"]) == (0, 2)
    # Texts are normalised so the AI sees the variants side by side.
    assert shoes["texts"] == [{"text": "dull", "count": 1}, {"text": "not bulled", "count": 1}]
    assert beret["share_pct"] == round(2 / 3 * 100, 1)


def test_an_absent_night_in_between_still_counts_as_running():
    # Persistence is over nights the cadet attended: missing a week doesn't mean
    # they fixed their shoes.
    sheets = [
        sheet("2026-01-07", mark(1, 6, fault("Shoes", "dull"))),
        sheet("2026-01-14", mark(1, absent=True, awol=True)),
        sheet("2026-01-21", mark(1, 5, fault("Shoes", "dull"))),
    ]
    a = audit_mod.build_audit(sheets, CADETS, "cadet", cin=1)
    assert a["faults"][0]["persistent_cadets"] == 1
    assert a["summary"]["awol"] == 1 and a["summary"]["attendance_pct"] == 66.7
    assert [t["absent"] for t in a["timeline"]] == [False, True, False]


def test_trend_compares_the_earlier_and_later_halves_per_cadet_inspected():
    shoes = fault("Shoes", "dull")
    sheets = [
        sheet("2026-01-07", mark(1, 5, shoes), mark(2, 5, shoes)),
        sheet("2026-01-14", mark(1, 5, shoes), mark(2, 5, shoes)),
        sheet("2026-01-21", mark(1, 8), mark(2, 8, fault("Beret", "x"))),
        sheet("2026-01-28", mark(1, 8), mark(2, 8, fault("Beret", "x"))),
    ]
    by_region = {f["region"]: f for f in audit_mod.build_audit(sheets, CADETS)["faults"]}
    assert (by_region["Shoes"]["earlier_pct"], by_region["Shoes"]["recent_pct"]) == (100.0, 0.0)
    assert by_region["Shoes"]["trend"] == "better"
    assert by_region["Beret"]["trend"] == "worse"


def test_no_trend_with_too_few_nights():
    sheets = [sheet(f"2026-01-0{d}", mark(1, 5, fault("Shoes", "dull"))) for d in (1, 2, 3)]
    a = audit_mod.build_audit(sheets, CADETS)
    assert a["faults"][0]["trend"] is None and a["faults"][0]["recent_pct"] is None


def test_squadron_audit_compares_flights_and_names_the_lowest_scorers():
    sheets = [sheet(
        "2026-01-07",
        mark(1, 9), mark(2, 4, fault("Shoes", "dull"), fault("Tie", "loose")),
        mark(3, 6, fault("Belt", "dirty")), mark(77, 5),
        mark(4, 1, fault("Shoes", "dull")),  # left the squadron — ignored
    )]
    a = audit_mod.build_audit(sheets, CADETS)
    assert [f["flight"] for f in a["flights"]] == ["A", "B", "Unassigned"]
    flight_a = a["flights"][0]
    assert (flight_a["cadets"], flight_a["score_avg"], flight_a["faults_per_cadet"]) == (2, 6.5, 1.0)
    assert a["summary"]["cadets"] == 4
    assert [c["name"] for c in a["cadets_to_watch"]] == ["Ben Baker", "Cadet 77", "Cal Cole", "Cpl Amy Able"]
    assert a["cadets_to_watch"][0]["top_faults"] == ["Shoes", "Tie"]
    assert a["baseline"] is None and a["timeline"] == []
    assert a["notes"] == ["Only 1 inspection in this audit, so there's no trend yet."]


def test_flight_audit_only_counts_that_flight_and_compares_with_the_squadron():
    sheets = [
        sheet("2026-01-07", mark(1, 8), mark(3, 4, fault("Belt", "dirty"))),
        sheet("2026-01-14", mark(1, 9)),  # B wasn't inspected that night
    ]
    a = audit_mod.build_audit(sheets, CADETS, "flight", flight="B")
    assert a["subject"] == "B Flight"
    assert a["dates"] == ["2026-01-07"]
    assert a["summary"]["score_avg"] == 4.0
    # Baseline is the squadron on B's nights only, not the night B missed.
    assert a["baseline"]["score_avg"] == 6.0
    assert a["flights"] == []


def test_cadet_audit_works_for_a_cadet_who_has_left():
    sheets = [sheet("2026-01-07", mark(4, 7, positive("Beret", "sharp")))]
    a = audit_mod.build_audit(sheets, CADETS, "cadet", cin=4)
    assert a["subject"] == "Zoë Öztürk"
    assert a["positives"] == [{"region": "Beret", "count": 1}]
    assert "No faults were logged." in a["notes"]


def test_nothing_recorded_for_the_subject_raises():
    with pytest.raises(audit_mod.NoData):
        audit_mod.build_audit([sheet("2026-01-07", mark(1, 5))], CADETS, "flight", flight="C")
    with pytest.raises(audit_mod.NoData):
        audit_mod.build_audit([], CADETS)


def test_a_cadet_absent_every_night_says_so_instead_of_reporting_no_faults():
    a = audit_mod.build_audit([sheet("2026-01-07", mark(2, absent=True))], CADETS, "cadet", cin=2)
    assert a["summary"]["score_avg"] is None and a["faults"] == []
    assert any("Absent from every inspection" in n for n in a["notes"])
    assert not any("No faults" in n for n in a["notes"])


def test_missing_regions_texts_and_comments_are_tolerated():
    sheets = [sheet("2026-01-07", {"cin": 1, "score": 5, "comments": [
        {"type": "fault", "region": None, "text": None}, {"type": "fault", "region": "  ", "text": "  "},
    ]}, {"cin": None, "score": 3}, {"cin": 2})]
    a = audit_mod.build_audit(sheets, CADETS)
    assert a["faults"][0]["region"] == "General" and a["faults"][0]["occurrences"] == 2
    assert a["faults"][0]["texts"] == []
    assert a["summary"]["cadets"] == 2


def test_markers_compared_only_from_nights_they_marked_alone():
    sheets = [
        sheet("2026-01-07", mark(1, 9), by="harsh@x"),
        sheet("2026-01-14", mark(1, 3), by="soft@x"),
        sheet("2026-01-21", mark(1, 5), by="harsh@x, soft@x"),
    ]
    a = audit_mod.build_audit(sheets, CADETS)
    assert a["markers"] == [
        {"marker": "harsh@x", "sheets": 1, "score_avg": 9.0},
        {"marker": "soft@x", "sheets": 1, "score_avg": 3.0},
    ]
    assert any("differ by 6.0 between inspectors" in n for n in a["notes"])


def test_one_marker_or_one_night_shows_no_marker_comparison():
    one_marker = [sheet("2026-01-07", mark(1, 9)), sheet("2026-01-14", mark(1, 3))]
    assert audit_mod.build_audit(one_marker, CADETS)["markers"] == []
    one_night = [sheet("2026-01-07", mark(1, 9), by="a@x, b@x")]
    assert audit_mod.build_audit(one_night, CADETS)["markers"] == []


# ── AI write-up ───────────────────────────────────────────────────────────────

def test_the_prompt_carries_tags_not_names_and_names_come_back(monkeypatch):
    seen = {}

    def fake_generate(prompt, system, **kw):
        seen["prompt"], seen["system"] = prompt, system
        return "### Top priorities\n1. [C1] needs shoes. Also C2, not C9.", "fake-model"

    monkeypatch.setattr(audit_mod, "generate", fake_generate)
    sheets = [sheet("2026-01-07", mark(1, 4, fault("Shoes", "Dull")), mark(4, 2), mark(3, 6))]
    cadets = {**CADETS, 4: {**CADETS[4], "banned": False}}
    a = audit_mod.build_audit(sheets, cadets)
    out = audit_mod.write_up(a)

    prompt = seen["prompt"]
    for name in ("Amy", "Able", "Zoë", "Öztürk", "Cal", "Cole"):
        assert name not in prompt
    assert "[C1]: average 2.0" in prompt and "[C2]: average 4.0" in prompt
    assert '"dull" ×1' in prompt and "persistent in 0" in prompt
    assert "Flights compared:" in prompt and "the whole squadron" in prompt
    assert out == {
        "analysis": "### Top priorities\n1. Zoë Öztürk needs shoes. Also Cpl Amy Able, not C9.",
        "model": "fake-model", "ai_error": None,
    }


def test_a_cadet_audit_prompt_never_names_the_cadet(monkeypatch):
    seen = {}

    def fake_generate(prompt, system, **kw):
        seen["prompt"] = prompt
        return "[C1] is doing well", "m"

    monkeypatch.setattr(audit_mod, "generate", fake_generate)
    sheets = [sheet(f"2026-01-0{d}", mark(1, 6)) for d in range(1, 6)]
    out = audit_mod.write_up(audit_mod.build_audit(sheets, CADETS, "cadet", cin=1))
    assert "one cadet, [C1]" in seen["prompt"] and "Amy" not in seen["prompt"]
    assert "the squadron on the same nights" in seen["prompt"]
    assert "5 nights, 2026-01-01 to 2026-01-05" in seen["prompt"]
    assert out["analysis"] == "Cpl Amy Able is doing well"


def test_a_failed_model_chain_keeps_the_figures(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(audit_mod, "generate", down)
    out = audit_mod.write_up(audit_mod.build_audit([sheet("2026-01-07", mark(1, 5))], CADETS))
    assert out["analysis"] is None and out["model"] is None
    assert "figures below are still accurate" in out["ai_error"]


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def squadron(db):
    for cin, c in CADETS.items():
        db.add(Cadet(cin=cin, **c))
    db.add_all([
        InspectionSheet(date=datetime(2026, 1, 7), submitted_by="a@x", submitted_at=datetime(2026, 1, 7),
                        data={"uniform": "blues", "marks": [mark(1, 6, fault("Shoes", "dull")), mark(3, 8)]}),
        InspectionSheet(date=datetime(2026, 2, 4), submitted_by="a@x", submitted_at=datetime(2026, 2, 4),
                        data={"uniform": "mtp", "marks": [mark(1, 7), mark(3, 5, fault("Boots", "muddy"))]}),
    ])
    db.commit()


@pytest.mark.parametrize("method", ["get", "post"])
def test_staff_only(api, squadron, method, monkeypatch):
    # The audit is on the site's staff-only /cadets/inspections page.
    monkeypatch.setattr(audit_mod, "generate", lambda *a, **k: ("ok", "m"))

    def call(headers):
        if method == "get":
            return api.get("/inspections/audit", headers=headers)
        return api.post("/inspections/audit", json={}, headers=headers)

    assert call({}).status_code == 401
    for persona in ("snco", "nco", "cadet"):
        assert call(api.as_(persona)).status_code == 403
    assert call(api.as_("staff")).status_code == 200


def test_get_returns_figures_without_spending_a_model_call(api, squadron, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("GET must not call the model")

    monkeypatch.setattr(audit_mod, "generate", boom)
    body = api.get("/inspections/audit", headers=STAFF).json()
    assert body["subject"] == "Squadron" and body["dates"] == ["2026-01-07", "2026-02-04"]
    assert "analysis" not in body
    assert {f["region"]: f["uniforms"] for f in body["faults"]} == {"Shoes": {"blues": 1}, "Boots": {"mtp": 1}}


def test_one_inspection_or_a_date_range(api, squadron):
    one = api.get("/inspections/audit?sheet_id=2", headers=STAFF).json()
    assert one["dates"] == ["2026-02-04"] and one["sheet_id"] == 2
    since = api.get("/inspections/audit?since=2026-01-08", headers=STAFF).json()
    assert since["dates"] == ["2026-02-04"]
    until = api.get("/inspections/audit?until=2026-01-31", headers=STAFF).json()
    assert until["dates"] == ["2026-01-07"]


def test_post_adds_the_write_up(api, squadron, monkeypatch):
    monkeypatch.setattr(audit_mod, "generate", lambda *a, **k: ("[C1] tidy boots", "fake-model"))
    body = api.post("/inspections/audit", json={"scope": "flight", "flight": "B"}, headers=STAFF).json()
    assert body["subject"] == "B Flight"
    assert (body["analysis"], body["model"], body["ai_error"]) == ("Cal Cole tidy boots", "fake-model", None)


def test_post_with_the_ai_down_still_returns_200_and_the_figures(api, squadron, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(audit_mod, "generate", down)
    res = api.post("/inspections/audit", json={"scope": "cadet", "cin": 1}, headers=STAFF)
    assert res.status_code == 200
    body = res.json()
    assert body["analysis"] is None and body["ai_error"] and body["summary"]["score_avg"] == 6.5


@pytest.mark.parametrize("params,status,detail", [
    ("scope=flight", 422, "Pick a flight"),
    ("scope=cadet", 422, "Pick a cadet"),
    ("scope=platoon", 422, None),
    ("sheet_id=1&since=2026-01-01", 422, "not both"),
    ("since=yesterday", 400, "Bad date"),
    ("sheet_id=99", 404, "Inspection not found"),
    ("scope=cadet&cin=99", 404, "Cadet not found"),
    ("scope=flight&flight=C", 404, "No inspections recorded"),
    ("since=2027-01-01", 404, "No inspections recorded"),
])
def test_rejections(api, squadron, params, status, detail):
    res = api.get(f"/inspections/audit?{params}", headers=STAFF)
    assert res.status_code == status
    if detail:
        assert detail in str(res.json()["detail"])


def test_post_validates_the_body_too(api, squadron):
    res = api.post("/inspections/audit", json={"scope": "cadet"}, headers=STAFF)
    assert res.status_code == 422 and "Pick a cadet" in str(res.json()["detail"])


def test_an_empty_database_is_a_404_not_a_crash(api):
    res = api.get("/inspections/audit", headers=STAFF)
    assert res.status_code == 404
