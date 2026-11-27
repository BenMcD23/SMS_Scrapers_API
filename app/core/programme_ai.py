"""The programme builder's AI draft: the shared model chain picks what to put
in each gap of a month, from the options the rules already worked out
(core/programme_builder.suggest), with the squadron's past programmes in front
of it so it follows how the squadron actually plans a month.

Nothing is trained. Each call sends the history (the same month last year and
the last three months, with rooms) along with the gaps, so the model always
uses the latest record, and its picks only ever come from the rules' options:
a bank session that exists, or joining a session already on the night. It
only suggests; staff accept the picks.

When every model fails or answers nonsense, the rules' own suggestions come
back with a note, so the button always does something useful.
"""

import logging
import re
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from core import programme_builder as pb
from core.llm import GROQ_MODEL, generate
from database.models import ProgrammeBankItem, ProgrammeDraft, ProgrammeNight

logger = logging.getLogger(__name__)

# Options per gap the model chooses from: enough choice, a prompt that stays small.
OPTIONS_PER_GAP = 6
HISTORY_NIGHTS = 40
# Measured on a month of 54 gaps (Nov 2026): Gemini 2.5 Flash answers in under a minute and
# Groq in seconds, while Nemotron and Gemini 3.8 timed out after 109 s each. So this
# caller skips the default leaders rather than keep staff waiting four minutes.
MODELS = ["gemini-2.5-flash", GROQ_MODEL]

SYSTEM_PROMPT = """You plan parade nights for an RAF Air Cadets squadron.
Each night has two halves; each flight (C is the probationary flight for new cadets, then A and B)
does one session per half. You get the squadron's past programmes, this month so far, a menu of
sessions, and this month's gaps, each with the option ids that fit it. For each gap pick one of its
options, or leave the gap out if none fit.

Plan like the squadron does:
- Follow the past programmes: what this month looked like last year (seasonal events, Remembrance,
  Christmas, passing out), what usually runs on which weekday, and which rooms sessions use.
- Give each flight a balanced month: a mix of kinds, not the same session twice in a month unless the
  past programmes run it most weeks (drill, inspection).
- Keep flights together when they'd do the same thing: pick the same session for them in the same half,
  or a "j" option to join one already placed.
- New probationary cadets (C) get the basics: drill, uniform, the RAFAC basics.

Reply with JSON only, no other text:
{"picks": [{"g": 0, "o": "b12", "why": "at most 8 words, for staff"}, ...]}"""


def _half_lines(nights, rows) -> list[str]:
    """"Wed 03 Dec 2025 1st: A+B Drill @Main Parade Hall; C Uniform", a line
    per half-night. ``rows(night)`` gives (period, flight, title, room) for it."""
    lines = []
    for n in nights:
        by_half: dict[int | None, dict[tuple, list[str]]] = defaultdict(lambda: defaultdict(list))
        for period, flight, title, room in rows(n):
            by_half[period][(title, room)].append(flight)
        for period in sorted(by_half, key=lambda p: p or 0):
            parts = [f"{'+'.join(sorted(set(f)))} {title}" + (f" @{room}" if room else "")
                     for (title, room), f in by_half[period].items()]
            half = {1: "1st", 2: "2nd"}.get(period, "all night")
            lines.append(f"{n.date:%a %d %b %Y} {half}: " + "; ".join(parts))
    return lines


def _history(db: Session, month: str) -> list[str]:
    """The same month last year, then the three months before this one."""
    first, after = pb.month_bounds(month)
    year_ago = (date(first.year - 1, first.month, 1), date(after.year - 1, after.month, 1))
    recent = (date(first.year - (first.month <= 3), (first.month - 4) % 12 + 1, 1), first)
    lines = []
    for label, (start, end) in (("Same month last year", year_ago), ("The last three months", recent)):
        nights = (db.query(ProgrammeNight).filter(ProgrammeNight.date >= start, ProgrammeNight.date < end)
                  .order_by(ProgrammeNight.date).limit(HISTORY_NIGHTS).all())
        found = _half_lines(nights, lambda n: [(s.period, s.flight, s.activity, s.room) for s in n.sessions])
        lines += [f"## {label}", *(found or ["(nothing in the record)"])]
    return lines


def _option_id(option: dict) -> str:
    return f"j{option['slot_id']}" if option["kind"] == "join" else f"b{option['bank_item_id']}"


def _prompt(db: Session, draft: ProgrammeDraft, ctx: pb.Context, gaps: list[dict]) -> str:
    def room(room_id):
        return ctx.rooms[room_id].name if room_id in ctx.rooms else None

    def placed(n):
        return [(s.period, f, s.title, room(s.room_id)) for s in n.slots for f in pb.slot_flights(s, ctx.flights)]

    menu: dict[str, str] = {}
    gap_lines = []
    for g, gap in enumerate(gaps):
        ids = []
        for option in gap["options"]:
            oid = _option_id(option)
            where = room(option.get("room_id"))
            what = f"join the {option['title']} already on" if option["kind"] == "join" else option["title"]
            menu.setdefault(oid, f"{oid} {what} [{option['category']}]" + (f" @{where}" if where else ""))
            ids.append(oid)
        day = date.fromisoformat(gap["date"])
        gap_lines.append(f"g{g} {day:%a %d %b} {'1st' if gap['period'] == 1 else '2nd'} {gap['flight']}: "
                         + " ".join(ids))
    return "\n".join(["# Past programmes", *_history(db, draft.month), "",
                      "# This month so far", *(_half_lines(draft.nights, placed) or ["(nothing yet)"]), "",
                      "# Menu", *menu.values(), "", "# Gaps", *gap_lines])


# One pick, as written: {"g": 3, "o": "b12", "why": "..."} with "why" optional.
PICK_RE = re.compile(r'\{\s*"g"\s*:\s*"?g?(\d+)"?\s*,\s*"o"\s*:\s*"([bj]\d+)"\s*(?:,\s*"why"\s*:\s*"((?:[^"\\]|\\.)*)")?')


def _picks(reply: str, gaps: list[dict]) -> dict[int, tuple[int, str]]:
    """{gap: (option index, why)} for each complete pick that names a real gap
    and one of its options. Read pick by pick, so an answer in a code fence or
    cut off part-way still gives every pick before the cut."""
    out = {}
    for g, oid, why in PICK_RE.findall(reply):
        g = int(g)
        if not 0 <= g < len(gaps) or g in out:
            continue
        o = next((i for i, option in enumerate(gaps[g]["options"]) if _option_id(option) == oid), None)
        if o is not None:
            out[g] = (o, " ".join(why.split())[:120])
    if not out and "picks" not in reply:
        raise ValueError("no picks in the reply")
    return out


def draft(db: Session, draft: ProgrammeDraft, ctx: pb.Context, bank: list[ProgrammeBankItem]) -> dict:
    """{suggestions, ai, picked, note}: the rules' suggestions, re-ordered so
    each gap's first option is the model's pick (marked ``ai`` with its
    reason). Gaps the model left out keep the rules' order."""
    gaps = pb.suggest(draft, ctx, bank, per_cell=OPTIONS_PER_GAP)
    if not gaps:
        return {"suggestions": [], "ai": False, "picked": 0, "note": "No gaps to fill, or nothing in the bank fits."}

    def rules(note: str) -> dict:
        return {"suggestions": [{**g, "options": g["options"][:3]} for g in gaps], "ai": False, "picked": 0,
                "note": note}

    try:
        # Room for a thinking model's reasoning as well as a pick per gap.
        reply, model = generate(_prompt(db, draft, ctx, gaps), SYSTEM_PROMPT, temperature=0.3, max_tokens=16000,
                                groq_max_tokens=6000, models=MODELS)
        picks = _picks(reply, gaps)
    except RuntimeError as e:
        logger.warning(f"programme AI draft: every model failed: {e}")
        return rules("The AI isn't answering just now, so these are the usual suggestions.")
    except ValueError:
        logger.warning("programme AI draft: unreadable reply")
        return rules("The AI's answer couldn't be read, so these are the usual suggestions.")

    out = []
    for g, gap in enumerate(gaps):
        options = gap["options"]
        if g in picks:
            o, why = picks[g]
            chosen = {**options[o], "ai": True, "reason": why or options[o]["reason"]}
            options = [chosen, *(x for i, x in enumerate(options) if i != o)]
        out.append({**gap, "options": options[:3]})
    logger.info(f"programme AI draft: {model} picked {len(picks)} of {len(gaps)} gaps")
    return {"suggestions": out, "ai": True, "picked": len(picks),
            "note": None if picks else "The AI had nothing to add, so these are the usual suggestions."}
