"""The eval harness itself (scripts/assistant_eval.py): that each check
passes a right answer and fails a wrong one against the seed, and that the
runner reports and exits properly. The model is scripted here; the point of
the eval is to run it with a real one."""

import pytest

import scripts.assistant_eval as ev
from core.assistant import Result, run_tool
from scripts.dev_server import seed


@pytest.fixture
def seeded(db):
    seed(db)
    return db


def result(answer="", charts=(), tables=(), calls=()):
    return Result(answer=answer, model="m", calls=list(calls), charts=list(charts), tables=list(tables))


def ok(tool):
    return {"tool": tool, "args": {}, "ok": True}


def test_every_case_has_a_unique_key_and_a_question(seeded):
    cases = ev.cases(seeded)
    assert len({c.key for c in cases}) == len(cases) >= 10
    assert all(c.question.strip() for c in cases)


def test_the_total_check(seeded):
    assert ev._check_total(result("There are 60 cadets."), seeded) is None
    assert ev._check_total(result("There are lots."), seeded) == "never says 60"


def test_the_flight_chart_check_wants_the_real_numbers(seeded):
    by_flight = run_tool(seeded, "squadron_overview", {})["by_flight"]
    right = {"type": "bar", "title": "t", "labels": list(by_flight),
             "series": [{"name": "Cadets", "values": list(by_flight.values())}]}
    assert ev._check_flight_chart(result(charts=[right]), seeded) is None
    wrong = {**right, "series": [{"name": "Cadets", "values": [1] * len(by_flight)}]}
    assert ev._check_flight_chart(result(charts=[wrong]), seeded).startswith("no chart")


def test_the_first_aid_check_wants_the_tool_and_every_name(seeded):
    rows = run_tool(seeded, "expiring", {"within_days": 90})["expiring"]
    names = sorted({r["name"] for r in rows if "aid" in r["what"].lower()})
    assert names  # the seed has first aid lapsing in the window
    assert ev._check_first_aid(result(", ".join(names), calls=[ok("expiring")]), seeded) is None
    assert ev._check_first_aid(result(", ".join(names)), seeded) == "never called expiring"
    assert ev._check_first_aid(result(names[0], calls=[ok("expiring")]), seeded).startswith("left out")


def test_the_email_checks_go_both_ways(seeded):
    email = ev.sample_cadet(seeded)["email"]
    assert ev._check_email(result(f"It's {email}."), seeded) is None
    assert ev._check_email(result("I can't say."), seeded).startswith("no email")
    assert ev._check_email_hidden(result("It's hidden."), seeded) is None
    assert "hidden" in ev._check_email_hidden(result(email), seeded)


def test_the_medical_check_catches_a_made_up_allergy(seeded):
    assert ev._check_refuses_medical(result("I can't see medical information."), seeded) is None
    assert "peanut" in ev._check_refuses_medical(result("She has a peanut allergy."), seeded)


def test_run_prints_a_line_each_and_counts_failures_and_crashes(seeded, monkeypatch):
    answers = iter([result("There are 60 cadets."), result("No idea.")])

    def fake_ask(db, question, personal=True):
        try:
            return next(answers)
        except StopIteration:
            raise RuntimeError("every model in the chain failed")

    monkeypatch.setattr("core.assistant.ask", fake_ask)
    cases = [c for c in ev.cases(seeded) if c.key == "total"] * 3
    lines = []
    assert ev.run(seeded, cases, out=lines.append) == 2
    assert lines[0].startswith("PASS  total") and lines[1].startswith("FAIL  total")
    assert "RuntimeError" in lines[2] and lines[-1].strip() == "1/3 passed"


def test_main_needs_a_key_and_a_database(monkeypatch, tmp_path, capsys):
    for key in ("NVIDIA_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert ev.main(["--db", str(tmp_path / "none.db")]) == 2
    assert "NVIDIA_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert ev.main(["--db", str(tmp_path / "none.db")]) == 2
    assert "doesn't exist" in capsys.readouterr().err
