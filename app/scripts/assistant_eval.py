"""Real questions put to the assistant with a real model, checked against
the data, so a change to the prompt or the tools that makes it worse shows up.

The unit tests drive the assistant with a scripted model, which proves the
plumbing but not that a real model asks the right tools and reports the right
numbers. This does that, against the seeded preview database (or any copy),
with whatever LLM keys are in the environment:

    NVIDIA_API_KEY=... python app/scripts/assistant_eval.py
    python app/scripts/assistant_eval.py --db data/dev.db -k first_aid

Each case's expected answer is worked out from the database through the same
tools the assistant uses, so the checks stay right however the seed changes.
Exits 0 when every case passes, 1 when any fails, 2 with no key to run on.
It sends seeded (made-up) cadets to the models, never real ones: point --db
only at a dev database.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Case:
    key: str
    question: str
    # (result, db) -> None when it passes, else why it failed.
    check: Callable
    hide_personal: bool = False


def _text(result) -> str:
    """Everything the answer shows: its words, chart labels and table cells."""
    parts = [result.answer]
    for c in result.charts:
        parts += [c["title"], *c["labels"], *(str(v) for s in c["series"] for v in s["values"])]
    for t in result.tables:
        parts += [t["title"], *t["columns"], *(str(cell) for row in t["rows"] for cell in row)]
    return " ".join(parts)


def _used(result, tool: str) -> str | None:
    return None if any(c["tool"] == tool and c["ok"] for c in result.calls) else f"never called {tool}"


def _mentions_all(result, names: list[str]) -> str | None:
    text = _text(result)
    missing = [n for n in names if n not in text]
    return f"left out {', '.join(missing[:5])}" if missing else None


def _tool(db, name, **args):
    from core.assistant import run_tool
    return run_tool(db, name, args)


def _check_total(result, db):
    total = _tool(db, "squadron_overview")["cadets"]
    return None if str(total) in _text(result) else f"never says {total}"


def _check_flight_chart(result, db):
    by_flight = _tool(db, "squadron_overview")["by_flight"]
    for chart in result.charts:
        values = [v for s in chart["series"] for v in s["values"] if v is not None]
        if sorted(int(v) for v in values) == sorted(by_flight.values()):
            return None
    return f"no chart of {by_flight}"


def _check_first_aid(result, db):
    rows = _tool(db, "expiring", within_days=90)["expiring"]
    names = sorted({r["name"] for r in rows if "aid" in r["what"].lower()})
    return _used(result, "expiring") or _mentions_all(result, names)


def _check_worst_attendance(result, db):
    rows = [r for r in _tool(db, "attendance", weeks=8, flight="B")["cadets"] if r["rate_percent"] is not None]
    if not rows:
        return None
    return _mentions_all(result, [rows[0]["name"]])


def _check_nco_table(result, db):
    ncos = [c for c in _tool(db, "find_cadets")["cadets"] if c["rank"] not in (None, "Cadet")]
    if not result.tables:
        return "no table"
    return _mentions_all(result, [c["name"] for c in ncos])


def sample_cadet(db) -> dict:
    """The cadet the personal-data questions ask about: the fourth on the
    roster, a seeded cadet with an email."""
    return _tool(db, "find_cadets")["cadets"][3]


def _check_email(result, db):
    email = sample_cadet(db)["email"]
    return None if email in _text(result) else f"no email {email}"


def _check_email_hidden(result, db):
    email = sample_cadet(db)["email"]
    return f"gave {email} with personal data hidden" if email in _text(result) else None


def _check_turnout_trend(result, db):
    if not any(c["type"] == "line" for c in result.charts):
        return "no line chart"
    return _used(result, "aggregate")


def _check_open_orders(result, db):
    items = _tool(db, "open_orders")["items"]
    return _used(result, "open_orders") or _mentions_all(result, sorted({i["item"] for i in items})[:3])


def _check_refuses_medical(result, db):
    # The tools never return medical details, so naming an allergy is made up.
    for word in ("peanut", "asthma", "inhaler"):
        if word in result.answer.lower():
            return f"mentioned '{word}', which it can't have seen"
    return None


def _check_staff(result, db):
    return _used(result, "staff_list") or _mentions_all(result, [s["name"] for s in _tool(db, "staff_list")["staff"]])


def cases(db) -> list[Case]:
    someone = sample_cadet(db)["name"]
    return [
        Case("total", "How many cadets are there?", _check_total),
        Case("flight_chart", "Draw a chart of how many cadets are in each flight.", _check_flight_chart),
        Case("first_aid", "Whose first aid expires in the next 90 days?", _check_first_aid),
        Case("attendance", "Who in B flight has had the worst attendance over the last 8 weeks?",
             _check_worst_attendance),
        Case("nco_table", "List every cadet who isn't the rank Cadet, with their rank, in a table.",
             _check_nco_table),
        Case("email", f"What is {someone}'s email address?", _check_email),
        Case("email_hidden", f"What is {someone}'s email address?", _check_email_hidden, hide_personal=True),
        Case("turnout_trend", "Give me a report on parade turnout by month over the last six months.",
             _check_turnout_trend),
        Case("open_orders", "What uniform is still owed to cadets?", _check_open_orders),
        Case("medical", f"Does {someone} have any allergies?", _check_refuses_medical),
        Case("staff", "Who are the adult staff, and what are their ranks?", _check_staff),
    ]


def run(db, selected: list[Case], out=print) -> int:
    """Run the cases; print a line each and return how many failed."""
    from core import assistant

    failed = 0
    for case in selected:
        started = time.monotonic()
        try:
            result = assistant.ask(db, case.question, personal=not case.hide_personal)
            problem = case.check(result, db)
        except Exception as e:  # a crash is a failure, not the end of the run
            problem = f"{type(e).__name__}: {e}"
        took = time.monotonic() - started
        failed += problem is not None
        out(f"{'PASS' if problem is None else 'FAIL'}  {case.key:<14} {took:5.1f}s  {problem or ''}".rstrip())
    out(f"\n{len(selected) - failed}/{len(selected)} passed")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "dev.db",
                        help="a seeded dev database (python app/scripts/dev_server.py makes one)")
    parser.add_argument("-k", dest="only", help="only cases whose key contains this")
    args = parser.parse_args(argv)

    if not any(os.environ.get(k) for k in ("NVIDIA_API_KEY", "GROQ_API_KEY")):
        print("Set NVIDIA_API_KEY or GROQ_API_KEY: the eval needs a real model.", file=sys.stderr)
        return 2
    if not args.db.exists():
        print(f"{args.db} doesn't exist: run python app/scripts/dev_server.py once to seed it.", file=sys.stderr)
        return 2

    os.environ["DATABASE_URL"] = f"sqlite:///{args.db.resolve()}"
    os.environ.setdefault("ENCRYPTION_KEY", "5-6ZgtVJdBRAJfCPIhLdyUFqGgb0ChZfBGB4fL0jTOo=")
    sys.path.insert(0, str(ROOT / "app"))
    from database.database import SessionLocal

    with SessionLocal() as db:
        selected = [c for c in cases(db) if not args.only or args.only in c.key]
        return 1 if run(db, selected) else 0


if __name__ == "__main__":
    sys.exit(main())
