"""The squadron assistant's tools and the loop that drives them.

No network: core.assistant.generate is replaced by a scripted model that
replies with whatever each test lines up, and records the prompts it was sent.
"""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

import core.assistant as assistant
from core.assistant_tools import MAX_ROWS, TOOLS, describe_tools
from database.models import (
    Cadet,
    CadetAbsence,
    CadetAttendance,
    CadetMedical,
    CadetQualification,
    CadetWht,
    Event317,
    Location,
    StoresBox,
    StoresItem,
    StoresOrder,
    StoresOrderItem,
    StoresSection,
)

NOW = datetime.now()
TODAY = NOW.date()


def _cadet(db, cin, first, last, **kw):
    c = Cadet(cin=cin, first_name=first, last_name=last, **kw)
    db.add(c)
    return c


@pytest.fixture
def squadron(db):
    """Two flights, one cadet who's stopped coming, and every kind of personal
    data filled in, so a test can check none of it gets out."""
    _cadet(db, 1, "Amy", "Ashworth", rank="Cpl", flight="A", classification="Leading Cadet",
           email="amy@example.com", phone_number="07700900001", parent_emails="mum@example.com",
           date_of_birth=datetime(2010, 4, 5))
    _cadet(db, 2, "Ben", "Brook", rank="Cadet", flight="B")
    _cadet(db, 3, "Siân", "Ó Briain", rank="Cadet", flight="B", missing_since=NOW - timedelta(days=3))
    db.add(CadetMedical(cadet_id=1, allergy_name="Peanuts", details="Carries two pens"))
    for w in range(6):
        night = NOW - timedelta(weeks=w, days=1)
        db.add(CadetAttendance(cadet_id=1, date=night, status="Present Correctly Dressed"))
        db.add(CadetAttendance(cadet_id=2, date=night, status="Absent" if w < 4 else "Present Correctly Dressed"))
    db.add(CadetAbsence(cadet_id=2, date_from=NOW, date_to=NOW + timedelta(days=7),
                        reason="Hospital appointment", scraped_at=NOW))
    db.add(CadetQualification(cadet_id=1, qual_type="St John Youth First Aid", status="true",
                              date_achieved=NOW - timedelta(days=700), date_expires=NOW + timedelta(days=20)))
    db.add(CadetQualification(cadet_id=2, qual_type="Essential First Aid", status="true",
                              date_expires=NOW - timedelta(days=5)))
    db.add(CadetQualification(cadet_id=2, qual_type="Bronze Leadership", status="true"))
    # An old lapsed WHT superseded by a current one, and one about to lapse.
    db.add(CadetWht(cadet_id=1, weapon="L98A2", date_passed=TODAY - timedelta(days=400),
                    date_expires=TODAY - timedelta(days=218)))
    db.add(CadetWht(cadet_id=1, weapon="L98A2", date_passed=TODAY - timedelta(days=30),
                    date_expires=TODAY + timedelta(days=152)))
    db.add(CadetWht(cadet_id=2, weapon="Scorpion (single shot)", date_passed=TODAY - timedelta(days=170),
                    date_expires=TODAY + timedelta(days=12)))
    box = StoresBox(label="Box 1")
    section = StoresSection(box=box, label="Top")
    db.add_all([box, section, StoresItem(item_type="Beret", size="56", quantity=3, gender="unisex",
                                         box=box, section=section)])
    order = StoresOrder(cadet_id=2, created_at=NOW - timedelta(days=10))
    order.order_items = [StoresOrderItem(item_type="Beret", size="56"),
                         StoresOrderItem(item_type="Tie", size="", need_sizing=True),
                         StoresOrderItem(item_type="Belt", size="M", given_at=NOW)]
    db.add(order)
    loc = Location(first_line="RAF Leeming", postcode="DL7 9NJ")
    db.add(Event317(title="AEF day", reference="E1", adult_ic="Flt Lt Shah", contact_number=7700900999,
                    date_from=NOW + timedelta(days=14), date_to=NOW + timedelta(days=14, hours=8),
                    location=loc, cost=0, dress="Blues", description="Flying"))
    db.add(Event317(title="Last year's camp", reference="E0", adult_ic="x", contact_number=1,
                    date_from=NOW - timedelta(days=300), date_to=NOW - timedelta(days=293),
                    location=loc, cost=50, dress="MTP", description="Old"))
    db.commit()
    return db


def call(db, tool, **args):
    return assistant.run_tool(db, tool, args)


# ─── Tools ───────────────────────────────────────────────────────────────────

def test_find_cadets_matches_any_part_of_a_name_and_filters(squadron):
    assert [c["cin"] for c in call(squadron, "find_cadets", name="ash")["cadets"]] == [1]
    assert [c["cin"] for c in call(squadron, "find_cadets", flight="b")["cadets"]] == [2, 3]
    # Non-ASCII names are matched as typed.
    assert call(squadron, "find_cadets", name="siân ó")["cadets"][0]["cin"] == 3
    assert call(squadron, "find_cadets", name="nobody") == {"count": 0, "cadets": []}


def test_cadet_record_has_the_squadrons_view_of_one_cadet(squadron):
    rec = call(squadron, "cadet_record", cin=2)
    assert rec["badges"] == {"First Aid": "blue", "Leadership": "bronze"}
    assert rec["attendance_last_12_weeks"] == {"present": 2, "authorised": 0, "absent": 4}
    assert rec["booked_absences"] and "reason" not in rec["booked_absences"][0]
    [order] = rec["open_stores_orders"]
    assert [i["item"] for i in order["items"]] == ["Beret", "Tie", "Belt"]


def test_cadet_record_for_an_unknown_cin_says_how_to_find_one(squadron):
    assert "find_cadets" in call(squadron, "cadet_record", cin=999)["error"]


def test_expiring_lists_quals_and_only_the_latest_wht_per_weapon(squadron):
    out = call(squadron, "expiring", within_days=30)
    assert [(r["cin"], r["what"]) for r in out["expiring"]] == [
        (2, "WHT Scorpion (single shot)"), (1, "St John Youth First Aid")]
    lapsed = call(squadron, "expiring", within_days=30, include_expired=True)["expiring"]
    # The lapsed first aid shows; cadet 1's old L98A2 doesn't, they've retested.
    assert (2, "Essential First Aid") in [(r["cin"], r["what"]) for r in lapsed]
    assert not any(r["what"] == "WHT L98A2" for r in lapsed)


def test_attendance_puts_the_worst_first_and_can_filter(squadron):
    out = call(squadron, "attendance", weeks=8)
    # On no register at all comes first, then 33%, then 100%.
    assert [(r["cin"], r["rate_percent"]) for r in out["cadets"]] == [(3, None), (2, 33), (1, 100)]
    assert out["register_nights"] == 6
    assert [r["cin"] for r in call(squadron, "attendance", flight="A")["cadets"]] == [1]


def test_a_cadet_who_has_stopped_coming_is_kept_by_a_low_attendance_filter(squadron):
    # Bader can stop listing a lapsed cadet on registers altogether, so "no
    # rows" must read as the worst attendance, not be filtered out.
    squadron.add(CadetAttendance(cadet_id=3, date=NOW - timedelta(weeks=20), status="Present Correctly Dressed"))
    squadron.commit()
    out = call(squadron, "attendance", weeks=8, max_rate=50)["cadets"]
    assert [r["cin"] for r in out] == [3, 2]
    # When they were last there is from all time, not just the window.
    assert out[0]["last_present"] == (NOW - timedelta(weeks=20)).date().isoformat()


def test_badge_holders_both_ways(squadron):
    held = call(squadron, "badge_holders", badge="first_aid")["cadets"]
    assert [(r["cin"], r["level"]) for r in held] == [(1, "bronze"), (2, "blue")]
    assert [r["cin"] for r in call(squadron, "badge_holders", badge="first_aid", level="bronze")["cadets"]] == [1]
    assert [r["cin"] for r in call(squadron, "badge_holders", badge="first_aid", holds=False)["cadets"]] == [3]
    assert "badges" in call(squadron, "badge_holders", badge="knitting")


def test_open_orders_skips_given_items_and_checks_the_shelf(squadron):
    items = call(squadron, "open_orders")["items"]
    assert [(i["item"], i["in_stock"]) for i in items] == [("Beret", 3), ("Tie", None)]
    assert items[1]["needs_sizing"] is True
    assert call(squadron, "stores_stock", item_type="BERET")["stock"][0]["quantity"] == 3


def test_upcoming_events_leaves_out_past_events_and_the_adults_phone(squadron):
    events = call(squadron, "upcoming_events")["events"]
    assert [e["title"] for e in events] == ["AEF day"]
    assert "contact_number" not in events[0] and "adult_ic" not in events[0]


def test_squadron_overview_counts_and_flags_cadets_missing_from_sms(squadron):
    out = call(squadron, "squadron_overview")
    assert out["cadets"] == 3 and out["by_flight"] == {"A": 1, "B": 2}
    assert [c["cin"] for c in out["not_seen_on_sms"]] == [3]


def test_no_tool_ever_returns_personal_data(squadron):
    # The answers go to a third-party model: contact details, DOB, medical and
    # absence reasons must never be in what a tool hands back.
    secrets = ["amy@example.com", "07700900001", "mum@example.com", "2010-04-05", "Peanuts",
               "Carries two pens", "Hospital", "7700900999"]
    out = json.dumps([
        call(squadron, "squadron_overview"), call(squadron, "find_cadets"),
        *(call(squadron, "cadet_record", cin=cin) for cin in (1, 2, 3)),
        call(squadron, "expiring", within_days=730, include_expired=True), call(squadron, "attendance"),
        *(call(squadron, "badge_holders", badge=b, holds=h) for b in ("first_aid", "leadership")
          for h in (True, False)),
        call(squadron, "stores_stock"), call(squadron, "open_orders"), call(squadron, "upcoming_events"),
    ], default=str)
    for secret in secrets:
        assert secret not in out


def test_every_tool_works_on_an_empty_database(db):
    # A fresh install, or a scrape that hasn't run yet: empty lists, not errors.
    for name in TOOLS:
        args = {"cin": 1} if name == "cadet_record" else {"badge": "first_aid"} if name == "badge_holders" else {}
        result = call(db, name, **args)
        assert name == "cadet_record" or "error" not in result, name


def test_long_lists_are_cut_and_say_so(db):
    db.add_all(Cadet(cin=i, first_name="C", last_name=str(i), flight="A") for i in range(MAX_ROWS + 5))
    db.commit()
    out = call(db, "find_cadets")
    assert out["count"] == MAX_ROWS + 5 and len(out["cadets"]) == MAX_ROWS
    assert "narrow" in out["truncated"]


@pytest.mark.parametrize("name, args, expected", [
    ("drop_tables", {}, "unknown tool"),
    (None, {}, "unknown tool"),
    ("cadet_record", {}, "bad arguments: cin"),
    ("cadet_record", {"cin": "abc"}, "bad arguments"),
    ("attendance", {"weeks": 500}, "bad arguments: weeks"),
])
def test_a_bad_call_comes_back_as_an_error_for_the_model(db, name, args, expected):
    assert expected in assistant.run_tool(db, name, args)["error"]


def test_a_tool_that_crashes_is_reported_not_raised(db, monkeypatch):
    def boom(_db, _args):
        raise ValueError("db gone")
    monkeypatch.setitem(TOOLS, "squadron_overview", replace(TOOLS["squadron_overview"], run=boom))
    assert assistant.run_tool(db, "squadron_overview", {}) == {"error": "squadron_overview failed; try a different question"}


def test_the_prompt_lists_every_tool_and_its_arguments():
    text = describe_tools()
    for name in TOOLS:
        assert f"- {name}:" in text
    assert "within_days: int (default 90)" in text
    assert "first_aid" in text  # badge keys are spelled out for the model


# ─── Reply parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ('{"answer": "Hi"}', {"answer": "Hi"}),
    ('```json\n{"calls": [{"tool": "x"}]}\n```', {"calls": [{"tool": "x"}]}),
    ('Sure! {"answer": "Two cadets."} Hope that helps', {"answer": "Two cadets."}),
    # A model that ignored the format has still usually answered.
    ("There are 12 cadets in B flight.", {"answer": "There are 12 cadets in B flight."}),
    ('{"answer": broken', {"answer": '{"answer": broken'}),
    ('{"something": "else"}', {"answer": '{"something": "else"}'}),
])
def test_parse_reply(text, expected):
    assert assistant.parse_reply(text) == expected


# ─── The loop ────────────────────────────────────────────────────────────────

@pytest.fixture
def model(monkeypatch):
    """A scripted model: each generate() returns the next reply in the list
    and records the prompt it was given."""
    state = {"replies": [], "prompts": [], "model": "z-ai/glm-5.3"}

    def fake_generate(prompt, system, **_kw):
        state["prompts"].append(prompt)
        assert "TOOLS" in system
        reply = state["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return (reply if isinstance(reply, str) else json.dumps(reply)), state["model"]

    monkeypatch.setattr(assistant, "generate", fake_generate)
    return state


def test_calls_tools_then_answers_from_their_results(squadron, model):
    model["replies"] = [
        {"calls": [{"tool": "find_cadets", "args": {"flight": "B"}}, {"tool": "attendance", "args": {"flight": "B"}}]},
        {"answer": "Ben Brook has missed four of the last six."},
    ]
    result = assistant.ask(squadron, "Who in B flight is missing parades?")
    assert result.answer == "Ben Brook has missed four of the last six."
    assert result.calls == [{"tool": "find_cadets", "args": {"flight": "B"}, "ok": True},
                            {"tool": "attendance", "args": {"flight": "B"}, "ok": True}]
    # The second round saw both results.
    assert '"name":"Ben Brook"' in model["prompts"][1] and "rate_percent" in model["prompts"][1]


def test_earlier_turns_are_in_the_prompt(db, model):
    model["replies"] = [{"answer": "Same for B."}]
    assistant.ask(db, "And B flight?", [{"role": "user", "content": "How many in A?"},
                                        {"role": "assistant", "content": "Four."}])
    assert "Staff: How many in A?\nAssistant: Four.\nStaff: And B flight?" in model["prompts"][0]


def test_a_failed_call_is_shown_to_the_model_so_it_can_retry(squadron, model):
    model["replies"] = [{"calls": [{"tool": "cadet_record", "args": {"cin": 999}}]},
                        {"calls": [{"tool": "find_cadets", "args": {"name": "amy"}}]},
                        {"answer": "Amy is CIN 1."}]
    result = assistant.ask(squadron, "Amy's record")
    assert [c["ok"] for c in result.calls] == [False, True]
    assert "no cadet with CIN 999" in model["prompts"][1]


def test_rounds_are_capped_and_the_last_one_must_answer(db, model):
    loop = {"calls": [{"tool": "squadron_overview", "args": {}}]}
    model["replies"] = [loop] * (assistant.MAX_ROUNDS - 1) + [{"answer": "Done."}]
    result = assistant.ask(db, "q")
    assert result.answer == "Done."
    assert len(model["prompts"]) == assistant.MAX_ROUNDS
    assert "No more tool calls" in model["prompts"][-1]


def test_a_model_still_asking_for_tools_on_the_last_round_gets_a_fallback_answer(db, model):
    model["replies"] = [{"calls": [{"tool": "squadron_overview"}]}] * assistant.MAX_ROUNDS
    result = assistant.ask(db, "q")
    assert "couldn't work that out" in result.answer
    assert len(result.calls) == assistant.MAX_ROUNDS - 1


def test_calls_per_round_are_capped(db, model):
    model["replies"] = [{"calls": [{"tool": "squadron_overview"}] * 10}, {"answer": "ok"}]
    assert len(assistant.ask(db, "q").calls) == assistant.MAX_CALLS_PER_ROUND


def test_junk_calls_dont_crash_the_loop(db, model):
    model["replies"] = [{"calls": ["not a dict", {"tool": 5, "args": "x"}]}, {"answer": "ok"}]
    result = assistant.ask(db, "q")
    assert result.answer == "ok" and [c["ok"] for c in result.calls] == [False, False]


def test_an_oversized_result_is_cut_before_the_next_prompt(db, model, monkeypatch):
    monkeypatch.setattr(assistant, "MAX_RESULT_CHARS", 50)
    model["replies"] = [{"calls": [{"tool": "squadron_overview"}]}, {"answer": "ok"}]
    assistant.ask(db, "q")
    assert "…(cut: too long" in model["prompts"][1]


def test_every_model_failing_is_raised_for_the_route_to_report(db, model):
    model["replies"] = [RuntimeError("every model in the chain failed")]
    with pytest.raises(RuntimeError):
        assistant.ask(db, "q")


def test_a_plain_text_reply_is_the_answer(db, model):
    model["replies"] = ["There are no cadets yet."]
    assert assistant.ask(db, "q").answer == "There are no cadets yet."


def test_dates_in_results_are_iso(squadron):
    # The prompt tells the model to write UK dates; it reads ISO ones.
    assert call(squadron, "cadet_record", cin=1)["weapon_handling_tests"][0]["passed"] == (
        TODAY - timedelta(days=30)).isoformat()
    assert isinstance(date.fromisoformat(call(squadron, "squadron_overview")["today"]), date)
