"""The squadron assistant's tools and the loop that drives them.

No network: core.assistant.generate is replaced by a scripted model that
replies with whatever each test lines up, and records the prompts it was sent.
"""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

import core.assistant as assistant
from core.assistant_tools import MAX_CADETS, MAX_ROWS, TOOLS, describe_tools
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


def plumbing(reply):
    """Most tests here check the plumbing, not grounding, so a scripted bare
    answer says it needed no records; the grounding tests set
    "no_records_needed" themselves to say otherwise."""
    if isinstance(reply, dict) and "answer" in reply and "calls" not in reply:
        return {"no_records_needed": True, **reply}
    return reply


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


def _everything(db, personal):
    return json.dumps([
        assistant.run_tool(db, name, args, personal) for name, args in [
            ("squadron_overview", {}), ("find_cadets", {}),
            *(("cadet_record", {"cin": cin}) for cin in (1, 2, 3)),
            ("expiring", {"within_days": 730, "include_expired": True}), ("attendance", {}),
            *(("badge_holders", {"badge": b, "holds": h}) for b in ("first_aid", "leadership") for h in (True, False)),
            ("stores_stock", {}), ("open_orders", {}), ("upcoming_events", {}),
            ("aggregate", {"dataset": "cadets", "group_by": [{"field": "flight"}]}),
        ]
    ], default=str)


NEVER_SENT = ["07700900001", "mum@example.com", "Peanuts", "Carries two pens", "Hospital", "7700900999"]


def test_dates_of_birth_and_emails_are_given_but_never_phones_parents_or_medical(squadron):
    # Staff asked for DOBs and cadet emails; the rest stays out of what goes
    # to a third-party model whatever is asked.
    out = _everything(squadron, personal=True)
    assert "amy@example.com" in out and "2010-04-05" in out
    for secret in NEVER_SENT:
        assert secret not in out


def test_hide_personal_data_keeps_dates_of_birth_and_emails_out_too(squadron):
    out = _everything(squadron, personal=False)
    for secret in [*NEVER_SENT, "amy@example.com", "2010-04-05", '"age"']:
        assert secret not in out


def test_find_cadets_gives_age_and_filters_by_it(squadron):
    squadron.get(Cadet, 2).date_of_birth = datetime(TODAY.year - 13, 1, 1)
    squadron.commit()
    amy = call(squadron, "find_cadets", name="amy")["cadets"][0]
    assert amy["date_of_birth"] == "2010-04-05" and amy["email"] == "amy@example.com"
    assert amy["age"] == TODAY.year - 2010 - ((TODAY.month, TODAY.day) < (4, 5))
    assert [c["cin"] for c in call(squadron, "find_cadets", max_age=14)["cadets"]] == [2]
    # A cadet with no date of birth isn't guessed into an age band.
    assert 3 not in [c["cin"] for c in call(squadron, "find_cadets", min_age=0)["cadets"]]
    # Age filters still work with personal data hidden; the ages just aren't shown.
    hidden = assistant.run_tool(squadron, "find_cadets", {"max_age": 14}, False)["cadets"]
    assert [c["cin"] for c in hidden] == [2] and "age" not in hidden[0]


def test_aggregate_counts_and_trends_through_the_dashboard_allowlist(squadron):
    by_flight = call(squadron, "aggregate", dataset="cadets", group_by=[{"field": "flight"}])
    assert [(r["keys"], r["value"]) for r in by_flight["rows"]] == [(["A"], 1), (["B"], 2)]
    turnout = call(squadron, "aggregate", dataset="cadet_attendance", measure="turnout",
                   filters=[{"field": "flight", "op": "eq", "value": "A"}])
    assert turnout["rows"][0]["value"] == 100 and turnout["unit"] == "%"
    monthly = call(squadron, "aggregate", dataset="cadet_attendance", range_days=365,
                   group_by=[{"field": "date", "grain": "month"}])
    assert sum(r["value"] for r in monthly["rows"]) == 12


@pytest.mark.parametrize("args, expected", [
    ({"dataset": "medical"}, "Unknown dataset"),
    ({"dataset": "cadets", "group_by": [{"field": "email"}]}, "can't be grouped by 'email'"),
    ({"dataset": "cadets", "measure": "sum"}, "no measure"),
    ({"dataset": "cadets", "filters": [{"field": "joined_on", "op": "gt", "value": "soon"}]}, "isn't a date"),
])
def test_aggregate_refuses_anything_off_the_allowlist(squadron, args, expected):
    # The allowlist is what keeps contact and medical columns unreachable.
    assert expected in call(squadron, "aggregate", **args)["error"]


def test_every_tool_works_on_an_empty_database(db):
    # A fresh install, or a scrape that hasn't run yet: empty lists, not errors.
    # rafac_docs is a network call, tested with a fake in test_assistant_tools.py.
    for name in [n for n in TOOLS if n != "rafac_docs"]:
        args = {"cadet_record": {"cin": 1}, "badge_holders": {"badge": "first_aid"},
                "aggregate": {"dataset": "cadet_attendance"}}.get(name, {})
        result = call(db, name, **args)
        assert name == "cadet_record" or "error" not in result, name


def test_long_lists_are_cut_and_say_so(db):
    # Rows a tool returns are capped; the cadet list a little higher, so a
    # whole squadron still fits in one answer.
    db.add_all(Cadet(cin=i, first_name="C", last_name=str(i), flight="A") for i in range(MAX_CADETS + 5))
    db.commit()
    out = call(db, "find_cadets")
    assert out["count"] == MAX_CADETS + 5 and len(out["cadets"]) == MAX_CADETS
    assert "narrow" in out["truncated"]
    assert len(call(db, "attendance")["cadets"]) == MAX_ROWS


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
    def boom(_db, _args, _personal):
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
        return (reply if isinstance(reply, str) else json.dumps(plumbing(reply))), state["model"]

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
    model["replies"] = [LOOK, "There are no cadets yet."]
    assert assistant.ask(db, "q").answer == "There are no cadets yet."


def test_dates_in_results_are_iso(squadron):
    # The prompt tells the model to write UK dates; it reads ISO ones.
    assert call(squadron, "cadet_record", cin=1)["weapon_handling_tests"][0]["passed"] == (
        TODAY - timedelta(days=30)).isoformat()
    assert isinstance(date.fromisoformat(call(squadron, "squadron_overview")["today"]), date)


# A first round that reads something, so what follows is a grounded answer.
LOOK = {"calls": [{"tool": "squadron_overview", "args": {}}]}


# ─── Charts, tables and follow-ups ───────────────────────────────────────────

BAR = {"type": "bar", "title": "Cadets by flight", "labels": ["A", "B"],
       "series": [{"name": "Cadets", "values": [1, 2]}]}
TABLE = {"title": "B flight", "columns": ["Name", "Rate"], "rows": [["Ben Brook", 33], ["Siân Ó Briain", None]]}


def test_an_answer_carries_its_charts_tables_and_follow_ups(db, model):
    model["replies"] = [LOOK, {"answer": "B is bigger.", "charts": [BAR], "tables": [TABLE],
                         "follow_ups": [" And by rank? ", "", 5, "Turnout by month?"]}]
    result = assistant.ask(db, "Report on flights")
    assert result.charts == [{**BAR, "series": [{"name": "Cadets", "values": [1.0, 2.0]}], "unit": ""}]
    assert result.tables == [TABLE]
    assert result.follow_ups == ["And by rank?", "Turnout by month?"]


@pytest.mark.parametrize("chart", [
    {**BAR, "series": [{"name": "Cadets", "values": [1]}]},  # one value for two labels
    {**BAR, "type": "radar"},
    {**BAR, "type": "pie", "series": [BAR["series"][0], BAR["series"][0]]},  # pie with two series
    {**BAR, "labels": [str(i) for i in range(5)], "type": "pie",
     "series": [{"name": "x", "values": list(range(5))}]},  # 5 slices
    {**BAR, "series": [{"name": f"s{i}", "values": [1, 2]} for i in range(5)]},  # 5 series
    {**BAR, "labels": []},
    "not a chart",
])
def test_a_chart_that_doesnt_fit_is_dropped_and_the_answer_kept(db, model, chart):
    model["replies"] = [LOOK, {"answer": "Here.", "charts": [chart, BAR]}]
    result = assistant.ask(db, "q")
    assert result.answer == "Here." and [c["title"] for c in result.charts] == ["Cadets by flight"]


def test_a_ragged_table_is_dropped(db, model):
    model["replies"] = [LOOK, {"answer": "Here.", "tables": [{**TABLE, "rows": [["Ben"]]}], "charts": "nope"}]
    result = assistant.ask(db, "q")
    assert result.tables == [] and result.charts == []


def test_charts_tables_and_follow_ups_are_capped(db, model):
    model["replies"] = [LOOK, {"answer": "x", "charts": [BAR] * 9, "tables": [TABLE] * 9, "follow_ups": ["a"] * 9}]
    result = assistant.ask(db, "q")
    assert (len(result.charts), len(result.tables), len(result.follow_ups)) == (
        assistant.MAX_CHARTS, assistant.MAX_TABLES, assistant.MAX_FOLLOW_UPS)


def test_hide_personal_reaches_the_tools(squadron, model):
    model["replies"] = [{"calls": [{"tool": "find_cadets", "args": {"name": "amy"}}]}, {"answer": "ok"}]
    assistant.ask(squadron, "Amy's email?", personal=False)
    assert "amy@example.com" not in model["prompts"][1]
    model["replies"] = [{"calls": [{"tool": "find_cadets", "args": {"name": "amy"}}]}, {"answer": "ok"}]
    assistant.ask(squadron, "Amy's email?")
    assert "amy@example.com" in model["prompts"][3]


# ─── Steps, models and cadet links ───────────────────────────────────────────

def test_ask_steps_reports_each_round_and_tool_before_the_result(squadron, model):
    model["replies"] = [{"calls": [{"tool": "expiring"}, {"tool": "nope"}]}, {"answer": "Done."}]
    events = list(assistant.ask_steps(squadron, "q"))
    assert [e.label for e in events[:-1]] == [
        "Thinking", "Checking what's expiring", "Looking it up", "Writing the answer"]
    assert isinstance(events[-1], assistant.Result)


def test_every_tool_has_a_progress_label():
    assert set(assistant.STEPS) == set(TOOLS)


def test_the_assistant_leaves_gemini_out_of_its_chain(db, monkeypatch):
    # Its prompts carry dates of birth and emails; Gemini's free tier may use
    # what it's sent.
    seen = {}

    def fake_generate(prompt, system, **kw):
        seen.update(kw)
        return '{"answer": "ok"}', "z-ai/glm-5.3"

    monkeypatch.setattr(assistant, "generate", fake_generate)
    assistant.ask(db, "q")
    assert seen["models"] == assistant.MODELS
    assert not any("gemini" in m for m in assistant.MODELS)


def test_cadets_named_in_the_answer_or_a_table_are_returned_to_link(squadron, model):
    model["replies"] = [{"calls": [{"tool": "find_cadets"}, {"tool": "staff_list"}]},
                        {"answer": "Amy Ashworth leads.", "tables": [
                            {"title": "t", "columns": ["Name"], "rows": [["Siân Ó Briain"]]}]}]
    result = assistant.ask(squadron, "q")
    assert result.cadets == [{"cin": 1, "name": "Amy Ashworth"}, {"cin": 3, "name": "Siân Ó Briain"}]


# ─── Grounding: no made-up answers ───────────────────────────────────────────

# What the backup model sent in prod for "whose first aid or WHTs expire in
# the next three months?": no tool calls, invented cadets, CINs and dates.
MADE_UP = {"answer": "- Cpl James Taylor (CIN 1023) – First Aid (expires 15 May 2026)\n"
                     "- Cadet Emily Brown (CIN 1087) – WHT – Rifle (expires 02 Jun 2026)",
           "no_records_needed": False}


def test_an_answer_that_read_no_records_is_sent_back_and_a_looked_up_one_used(squadron, model):
    model["replies"] = [MADE_UP, {"calls": [{"tool": "expiring", "args": {"within_days": 90}}]},
                        {"answer": "Amy Ashworth (CIN 1): St John Youth First Aid."}]
    result = assistant.ask(squadron, "Whose first aid expires soon?")
    assert result.answer == "Amy Ashworth (CIN 1): St John Youth First Aid."
    assert "Your last reply answered without reading any records" in model["prompts"][1]
    assert "James Taylor" not in result.answer


def test_a_model_that_never_looks_gets_no_answer_through(db, model):
    # Every round answers from nothing: staff get "couldn't check", never the invention.
    model["replies"] = [MADE_UP] * assistant.MAX_ROUNDS
    result = assistant.ask(db, "Whose first aid expires soon?")
    assert result.answer == assistant.UNCHECKED
    assert result.charts == result.tables == result.follow_ups == result.cadets == []
    assert len(model["prompts"]) == assistant.MAX_ROUNDS


def test_a_plain_text_answer_from_nothing_is_not_trusted_either(db, model):
    # gpt-oss sometimes ignores the JSON format and just writes; that's still an answer from nothing.
    model["replies"] = ["Cpl James Taylor (CIN 1023) expires 15 May 2026."] * assistant.MAX_ROUNDS
    assert assistant.ask(db, "q").answer == assistant.UNCHECKED


def test_a_cin_no_tool_returned_is_rejected(squadron, model):
    looked = {"calls": [{"tool": "find_cadets", "args": {}}]}
    model["replies"] = [looked, {"answer": "Amy Ashworth (CIN 1) and Jo Bloggs (CIN 9999)."},
                        {"answer": "Amy Ashworth (CIN 1)."}]
    result = assistant.ask(squadron, "Who's in A?")
    assert result.answer == "Amy Ashworth (CIN 1)."
    assert "gave CIN 9999, which no tool returned" in model["prompts"][2]


def test_an_invented_cin_in_a_table_counts_too(squadron, model):
    looked = {"calls": [{"tool": "find_cadets", "args": {}}]}
    table = {"title": "t", "columns": ["Name", "CIN"], "rows": [["Jo Bloggs", "CIN 9999"]]}
    model["replies"] = [looked] + [{"answer": "See the table.", "tables": [table]}] * (assistant.MAX_ROUNDS - 1)
    assert assistant.ask(squadron, "q").answer == assistant.UNCHECKED


def test_a_greeting_needs_no_records_but_carries_no_made_up_figures(db, model):
    model["replies"] = [{"answer": "Hello! Ask me about the squadron.", "no_records_needed": True,
                         "charts": [BAR], "tables": [TABLE]}]
    result = assistant.ask(db, "hi")
    assert result.answer == "Hello! Ask me about the squadron."
    # Nothing was read and there's no chat to draw on, so any figures are invented.
    assert result.charts == [] and result.tables == []


def test_reshaping_an_earlier_answer_needs_no_new_lookup(db, model):
    history = [{"role": "user", "content": "Cadets by flight?"},
               {"role": "assistant", "content": "A 1, B 2 (Amy Ashworth, CIN 1)."}]
    model["replies"] = [{"answer": "As a pie chart.", "no_records_needed": True, "charts": [BAR]}]
    result = assistant.ask(db, "Make that a pie chart", history)
    assert result.charts and result.answer == "As a pie chart."


def test_a_cin_from_earlier_in_the_chat_is_known(db, model):
    history = [{"role": "assistant", "content": "Amy Ashworth (CIN 2100004) is in A flight."}]
    model["replies"] = [{"answer": "Yes, CIN 2100004 is Amy.", "no_records_needed": True}]
    assert assistant.ask(db, "Is that Amy?", history).answer == "Yes, CIN 2100004 is Amy."


def test_a_lookup_that_failed_doesnt_count_as_reading_the_records(db, model):
    failed = {"calls": [{"tool": "cadet_record", "args": {"cin": 999}}]}
    model["replies"] = [failed] + [{"answer": "Cpl Bloggs is fine.", "no_records_needed": False}] * 4
    assert assistant.ask(db, "How's Bloggs?").answer == assistant.UNCHECKED


def test_the_prompt_forbids_answering_from_memory():
    assert "Call a tool before answering any question about cadets" in assistant.SYSTEM_PROMPT
    assert '"no_records_needed"' in assistant.SYSTEM_PROMPT
