"""Inspection audit: what needs improving most, for one cadet, one flight or the
whole squadron, over a single inspection night or all of them.

Code does the counting and the AI only does the judgement. Every figure in the
response is worked out here; the model is handed those figures and asked to
group the free-text faults ("boots not bulled", "dull toecaps"), rank the top
priorities and turn them into actions. So it can't invent a number, and one
model call per audit keeps the free-tier limits out of reach.

Cadets reach the model as [C1], [C2]… and their names are swapped back in on
the way out, so no cadet's name (they're minors) leaves the API.
"""

import logging
import re
from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from core.db import get_db
from core.llm import generate
from core.security import require_staff
from database.models import Cadet, InspectionSheet
from routers.inspections import _flight_order, _parse_date, _split_comments

logger = logging.getLogger(__name__)
router = APIRouter()

# Trends need an earlier and a later half with at least two nights each, or one
# odd night swings the whole "getting worse" verdict.
MIN_SHEETS_FOR_TREND = 4
# Percentage-point change in a fault's rate before it counts as worse/better.
TREND_THRESHOLD = 10
# Below this many nights the report says its trends are provisional.
FEW_SHEETS = 3
# Average-score gap between markers worth warning about: one harsh marker can
# otherwise read as a flight getting worse.
MARKER_SPREAD_WARNING = 1.5
MAX_FAULT_AREAS = 12
MAX_CADETS_TO_WATCH = 5


class AuditRequest(BaseModel):
    scope: Literal["cadet", "flight", "squadron"] = "squadron"
    flight: str | None = None
    cin: int | None = None
    # One inspection night, or every night (optionally between since and until).
    sheet_id: int | None = None
    since: str | None = None
    until: str | None = None

    @model_validator(mode="after")
    def _subject_given(self):
        if self.scope == "flight" and not self.flight:
            raise ValueError("Pick a flight to audit")
        if self.scope == "cadet" and self.cin is None:
            raise ValueError("Pick a cadet to audit")
        if self.sheet_id is not None and (self.since or self.until):
            raise ValueError("Audit one inspection or a date range, not both")
        return self


class NoData(Exception):
    """Nothing was recorded for the subject in the chosen inspections."""


def _cadet_name(c: dict) -> str:
    return f"{c['rank'] + ' ' if c.get('rank') else ''}{c['first_name']} {c['last_name']}"


def _pct(n: float, d: float) -> float:
    return round(n / d * 100, 1) if d else 0.0


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _records(sheets: list[dict], cadets: dict[int, dict], scope: str,
             flight: str | None, cin: int | None) -> list[dict]:
    """One row per cadet per night in scope, oldest first.

    Cadets who have left (banned) are dropped from flight and squadron audits so
    they don't drag a flight's figures, but a cadet audit names its cadet
    explicitly, so it still works for them."""
    out = []
    for s in sorted(sheets, key=lambda s: s["date"]):
        for m in s["marks"]:
            mcin = m.get("cin")
            if mcin is None:
                continue
            cadet = cadets.get(mcin)
            mflight = (cadet or {}).get("flight") or "Unassigned"
            if scope == "cadet":
                if mcin != cin:
                    continue
            else:
                if cadet and cadet.get("banned"):
                    continue
                if scope == "flight" and mflight != flight:
                    continue
            faults, positives = _split_comments(m.get("comments") or [])
            out.append({
                "date":      s["date"],
                "uniform":   s["uniform"],
                "cin":       mcin,
                "flight":    mflight,
                "absent":    bool(m.get("absent")),
                "awol":      bool(m.get("awol")),
                "score":     None if m.get("absent") else m.get("score"),
                "faults":    faults,
                "positives": positives,
            })
    return out


def _summary(records: list[dict]) -> dict:
    present = [r for r in records if not r["absent"]]
    scores = [float(r["score"]) for r in present if r["score"] is not None]
    faults = sum(len(r["faults"]) for r in present)
    return {
        "sheets":            len({r["date"] for r in records}),
        "cadets":            len({r["cin"] for r in records}),
        "present":           len(present),
        "absent":            len(records) - len(present),
        "awol":              sum(1 for r in records if r["awol"]),
        "attendance_pct":    _pct(len(present), len(records)),
        "score_avg":         _avg(scores),
        "faults":            faults,
        "faults_per_cadet":  round(faults / len(present), 2) if present else 0.0,
        "positives":         sum(len(r["positives"]) for r in present),
    }


def _region(note: dict) -> str:
    return (note.get("region") or "General").strip() or "General"


def _fault_areas(records: list[dict], dates: list[str]) -> list[dict]:
    """Faults grouped by uniform area, the most entrenched first.

    Grouped by area rather than by text because inspectors word the same fault
    a dozen ways; the texts are listed under each area for the AI to merge.
    "Persistent" means faulted in that area on two nights running that the cadet
    attended: told about it and didn't fix it, which is why it ranks first."""
    present = [r for r in records if not r["absent"]]
    cadets_present = {r["cin"] for r in present}

    halves = None
    if len(dates) >= MIN_SHEETS_FOR_TREND:
        cut = dates[len(dates) // 2]
        halves = (
            [r for r in present if r["date"] < cut],
            [r for r in present if r["date"] >= cut],
        )

    by_cadet: dict[int, list[set[str]]] = {}
    for r in present:
        by_cadet.setdefault(r["cin"], []).append({_region(f) for f in r["faults"]})

    areas: dict[str, dict] = {}
    for r in present:
        for f in r["faults"]:
            a = areas.setdefault(_region(f), {
                "occurrences": 0, "cadets": set(), "texts": Counter(), "uniforms": Counter(),
            })
            a["occurrences"] += 1
            a["cadets"].add(r["cin"])
            text = (f.get("text") or "").strip().lower()
            if text:
                a["texts"][text] += 1
            a["uniforms"][r["uniform"]] += 1

    out = []
    for region, a in areas.items():
        persistent = sum(
            1 for nights in by_cadet.values()
            if any(region in x and region in y for x, y in zip(nights, nights[1:], strict=False))
        )
        entry = {
            "region":            region,
            "occurrences":       a["occurrences"],
            "cadets":            len(a["cadets"]),
            "share_pct":         _pct(len(a["cadets"]), len(cadets_present)),
            "persistent_cadets": persistent,
            "texts":             [{"text": t, "count": n} for t, n in a["texts"].most_common(6)],
            "uniforms":          dict(a["uniforms"]),
            "earlier_pct":       None,
            "recent_pct":        None,
            "trend":             None,
        }
        if halves:
            # Rate per cadet-night present, so a better-attended half isn't
            # mistaken for a worse one just because more cadets were inspected.
            rates = [
                _pct(sum(1 for r in half for f in r["faults"] if _region(f) == region), len(half))
                for half in halves
            ]
            entry["earlier_pct"], entry["recent_pct"] = rates
            diff = rates[1] - rates[0]
            entry["trend"] = (
                "worse" if diff >= TREND_THRESHOLD else "better" if diff <= -TREND_THRESHOLD else "steady"
            )
        out.append(entry)

    out.sort(key=lambda e: (-e["persistent_cadets"], -e["cadets"], -e["occurrences"], e["region"]))
    return out[:MAX_FAULT_AREAS]


def _positive_areas(records: list[dict]) -> list[dict]:
    counts = Counter(_region(p) for r in records if not r["absent"] for p in r["positives"])
    return [{"region": k, "count": n} for k, n in counts.most_common(6)]


def _flights(records: list[dict]) -> list[dict]:
    by_flight: dict[str, list[dict]] = {}
    for r in records:
        by_flight.setdefault(r["flight"], []).append(r)
    out = []
    for flight, rows in by_flight.items():
        top = _fault_areas(rows, sorted({r["date"] for r in rows}))
        out.append({"flight": flight, **_summary(rows), "top_fault": top[0]["region"] if top else None})
    out.sort(key=lambda f: _flight_order(f["flight"]))
    return out


def _cadets_to_watch(records: list[dict], cadets: dict[int, dict]) -> list[dict]:
    """The lowest average scorers — the cadets a flight's figures hide."""
    by_cin: dict[int, list[dict]] = {}
    for r in records:
        by_cin.setdefault(r["cin"], []).append(r)
    rows = []
    for cin, rs in by_cin.items():
        s = _summary(rs)
        if s["score_avg"] is None:
            continue
        regions = Counter(_region(f) for r in rs if not r["absent"] for f in r["faults"])
        rows.append({
            "cin":            cin,
            "name":           _cadet_name(cadets[cin]) if cin in cadets else f"Cadet {cin}",
            "flight":         rs[0]["flight"],
            "score_avg":      s["score_avg"],
            "present":        s["present"],
            "awol":           s["awol"],
            "attendance_pct": s["attendance_pct"],
            "top_faults":     [k for k, _ in regions.most_common(3)],
        })
    rows.sort(key=lambda c: (c["score_avg"], -c["awol"], c["name"]))
    return rows[:MAX_CADETS_TO_WATCH]


def _markers(sheets: list[dict], records: list[dict]) -> list[dict]:
    """Average score each inspector gave, from the nights they marked alone.

    A night two people marked can't be split between them (marks don't record
    who gave them), so only single-marker nights count."""
    dates = {r["date"] for r in records}
    by_marker: dict[str, dict] = {}
    for s in sheets:
        who = [w.strip() for w in (s.get("submitted_by") or "").split(",") if w.strip()]
        if len(who) != 1 or s["date"] not in dates:
            continue
        scores = [float(r["score"]) for r in records
                  if r["date"] == s["date"] and r["score"] is not None]
        m = by_marker.setdefault(who[0], {"marker": who[0], "sheets": 0, "scores": []})
        m["sheets"] += 1
        m["scores"].extend(scores)
    out = [
        {"marker": m["marker"], "sheets": m["sheets"], "score_avg": _avg(m["scores"])}
        for m in by_marker.values() if m["scores"]
    ]
    out.sort(key=lambda m: m["marker"])
    return out if len(out) > 1 else []


def build_audit(sheets: list[dict], cadets: dict[int, dict], scope: str = "squadron",
                flight: str | None = None, cin: int | None = None) -> dict:
    """Every figure the audit shows, from plain sheet and cadet dicts.

    ``sheets``: ``{id, date (ISO), uniform, submitted_by, marks}``.
    ``cadets``: cin → ``{first_name, last_name, rank, flight, banned}``.
    Raises NoData when nothing in ``sheets`` covers the subject."""
    records = _records(sheets, cadets, scope, flight, cin)
    if not records:
        raise NoData
    dates = sorted({r["date"] for r in records})
    summary = _summary(records)

    if scope == "cadet":
        subject = _cadet_name(cadets[cin]) if cin in cadets else f"Cadet {cin}"
    elif scope == "flight":
        subject = f"{flight} Flight"
    else:
        subject = "Squadron"

    audit = {
        "scope":    scope,
        "subject":  subject,
        "flight":   flight,
        "cin":      cin,
        "dates":    dates,
        "summary":  summary,
        "baseline": None,
        "faults":   _fault_areas(records, dates),
        "positives": _positive_areas(records),
        "flights":  _flights(records) if scope == "squadron" else [],
        "cadets_to_watch": _cadets_to_watch(records, cadets) if scope != "cadet" else [],
        "timeline": [
            {"date": r["date"], "score": r["score"], "absent": r["absent"], "awol": r["awol"]}
            for r in records
        ] if scope == "cadet" else [],
        "markers":  _markers(sheets, records) if len(dates) > 1 else [],
        "notes":    [],
    }

    if scope != "squadron":
        # The squadron on the same nights, so "6.8 average" has something to be
        # compared with.
        nights = set(dates)
        squadron = _records([s for s in sheets if s["date"] in nights], cadets, "squadron", None, None)
        if squadron:
            audit["baseline"] = {"label": "Squadron, same nights", **_summary(squadron)}

    if len(dates) < FEW_SHEETS:
        audit["notes"].append(
            f"Only {len(dates)} inspection{'' if len(dates) == 1 else 's'} in this audit, "
            "so there's no trend yet."
        )
    if summary["present"] == 0:
        audit["notes"].append("Absent from every inspection in this audit, so nothing was marked.")
    elif summary["faults"] == 0:
        audit["notes"].append("No faults were logged.")
    if audit["markers"]:
        avgs = [m["score_avg"] for m in audit["markers"]]
        spread = round(max(avgs) - min(avgs), 2)
        if spread >= MARKER_SPREAD_WARNING:
            audit["notes"].append(
                f"Average scores differ by {spread} between inspectors, so some differences "
                "may be down to who marked rather than the cadets."
            )
    return audit


# ── AI write-up ───────────────────────────────────────────────────────────────

AUDIT_SYSTEM_PROMPT = (
    "You are a senior air cadet uniform inspecting officer auditing inspection "
    "results for squadron staff. You decide what needs improving most and give "
    "practical actions. British English. Use only the figures you are given and "
    "never invent faults, numbers or cadets. Refer to individual cadets only by "
    "their tag exactly as written, e.g. [C3]."
)

AUDIT_PROMPT_TEMPLATE = """Audit the uniform inspection results below and work out what needs improving most.

Subject: {subject}
Inspections covered: {period}
{summary}
{baseline}
Faults by uniform area (most entrenched first). "persistent" = cadets faulted in
that area on two nights running, i.e. told and not fixed:
{faults}

Positive notes by area:
{positives}
{flights}{cadets}{notes}
Several fault texts within an area may describe the same problem in different
words; treat them as one.

Write a short report with these markdown sections:
### Top priorities
Up to three numbered items, most important first. For each: the area and the
actual fault, the evidence (how many cadets, how persistent, whether it's getting
worse) and one specific action, e.g. a short session, a demonstration or a
re-inspection. Rank persistent, widespread faults above one-offs.
### Going well
One or two lines on strengths or improvements.
### Next inspection
What to check first next time.

Keep it under 250 words. If there is very little data, say so and keep it brief."""


def _prompt(audit: dict, tags: dict[int, str]) -> str:
    s = audit["summary"]

    def summary_line(x: dict) -> str:
        avg = f"{x['score_avg']} / 10" if x["score_avg"] is not None else "no scores"
        return (f"{x['cadets']} cadet(s), {x['present']} inspected present, attendance "
                f"{x['attendance_pct']}%, {x['awol']} AWOL, average score {avg}, "
                f"{x['faults_per_cadet']} faults per cadet inspected")

    subject = {
        "cadet":    f"one cadet, {tags.get(audit['cin'], '[C1]')}",
        "flight":   audit["subject"],
        "squadron": "the whole squadron",
    }[audit["scope"]]
    dates = audit["dates"]
    period = dates[0] if len(dates) == 1 else f"{len(dates)} nights, {dates[0]} to {dates[-1]}"

    faults = "\n".join(
        f"- {f['region']}: {f['occurrences']} fault(s) on {f['cadets']} cadet(s) "
        f"({f['share_pct']}% of those inspected), persistent in {f['persistent_cadets']}"
        + (f", trend {f['trend']} ({f['earlier_pct']}% → {f['recent_pct']}% of inspections)"
           if f["trend"] else "")
        + ". Notes: " + ("; ".join(f"\"{t['text']}\" ×{t['count']}" for t in f["texts"]) or "none")
        for f in audit["faults"]
    ) or "- None logged"
    positives = "\n".join(f"- {p['region']} ×{p['count']}" for p in audit["positives"]) or "- None logged"

    baseline = ""
    if audit["baseline"]:
        baseline = f"For comparison, the squadron on the same nights: {summary_line(audit['baseline'])}.\n"

    flights = ""
    if audit["flights"]:
        flights = "\nFlights compared:\n" + "\n".join(
            f"- {f['flight']}: {summary_line(f)}; top fault area {f['top_fault'] or 'none'}"
            for f in audit["flights"]
        ) + "\n"

    cadets = ""
    if audit["cadets_to_watch"]:
        cadets = "\nLowest average scores:\n" + "\n".join(
            f"- {tags[c['cin']]}: average {c['score_avg']}, attendance {c['attendance_pct']}%, "
            f"{c['awol']} AWOL, most faults in {', '.join(c['top_faults']) or 'nothing'}"
            for c in audit["cadets_to_watch"]
        ) + "\n"

    notes = ""
    if audit["notes"]:
        notes = "\nCaveats:\n" + "\n".join(f"- {n}" for n in audit["notes"]) + "\n"

    return AUDIT_PROMPT_TEMPLATE.format(
        subject=subject, period=period, summary=f"Overall: {summary_line(s)}.",
        baseline=baseline, faults=faults, positives=positives,
        flights=flights, cadets=cadets, notes=notes,
    )


def _tags(audit: dict) -> dict[int, str]:
    cins = [audit["cin"]] if audit["scope"] == "cadet" else [c["cin"] for c in audit["cadets_to_watch"]]
    return {cin: f"[C{i}]" for i, cin in enumerate(cins, start=1)}


def _untag(text: str, tags: dict[int, str], names: dict[int, str]) -> str:
    """Put names back where the model wrote a tag. It sometimes drops the
    brackets, so a bare C3 is matched too (a flight letter never has a digit)."""
    by_number = {tag[2:-1]: names[cin] for cin, tag in tags.items()}

    def swap(m: re.Match) -> str:
        return by_number.get(m.group(1) or m.group(2), m.group(0))

    return re.sub(r"\[C(\d+)\]|\bC(\d+)\b", swap, text)


def write_up(audit: dict) -> dict:
    """The AI's ranking of the audit. A failed model chain is reported in
    ``ai_error`` rather than raised: the figures are still worth showing."""
    tags = _tags(audit)
    names = {c["cin"]: c["name"] for c in audit["cadets_to_watch"]}
    if audit["scope"] == "cadet":
        names[audit["cin"]] = audit["subject"]
    try:
        text, model = generate(_prompt(audit, tags), AUDIT_SYSTEM_PROMPT, temperature=0.4,
                               max_tokens=6000, groq_max_tokens=2000)
    except RuntimeError as e:
        logger.error(f"inspection audit write-up failed: {e}")
        return {"analysis": None, "model": None, "ai_error": "The AI couldn't write this audit right now. The figures below are still accurate."}
    return {"analysis": _untag(text, tags, names), "model": model, "ai_error": None}


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _audit(req: AuditRequest, db: Session) -> dict:
    q = db.query(InspectionSheet)
    if req.sheet_id is not None:
        q = q.filter(InspectionSheet.id == req.sheet_id)
    if req.since:
        q = q.filter(InspectionSheet.date >= _parse_date(req.since))
    if req.until:
        q = q.filter(InspectionSheet.date <= _parse_date(req.until))
    rows = q.order_by(InspectionSheet.date).all()
    if req.sheet_id is not None and not rows:
        raise HTTPException(status_code=404, detail="Inspection not found")

    if req.scope == "cadet" and not db.query(Cadet).filter(Cadet.cin == req.cin).first():
        raise HTTPException(status_code=404, detail="Cadet not found")

    sheets = [{
        "id":           s.id,
        "date":         s.date.date().isoformat(),
        "uniform":      (s.data or {}).get("uniform", "blues"),
        "submitted_by": s.submitted_by,
        "marks":        (s.data or {}).get("marks", []),
    } for s in rows]
    cins = {m.get("cin") for s in sheets for m in s["marks"]} | {req.cin}
    cadets = {
        c.cin: {"first_name": c.first_name, "last_name": c.last_name, "rank": c.rank,
                "flight": c.flight, "banned": bool(c.banned)}
        for c in db.query(Cadet).filter(Cadet.cin.in_([c for c in cins if c is not None])).all()
    }
    try:
        audit = build_audit(sheets, cadets, req.scope, req.flight, req.cin)
    except NoData:
        raise HTTPException(status_code=404, detail="No inspections recorded for that selection")
    audit["sheet_id"] = req.sheet_id
    return audit


@router.get("/inspections/audit")
def audit_figures(
    req: Annotated[AuditRequest, Query()],
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The audit's figures without the AI write-up, so the page shows the
    evidence straight away and only spends a model call when asked."""
    return _audit(req, db)


@router.post("/inspections/audit")
def audit_with_ai(  # sync on purpose — the slow AI call runs in the threadpool
    req: AuditRequest,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The audit's figures plus the AI's top priorities and actions."""
    audit = _audit(req, db)
    logger.info(f"inspection audit ({audit['subject']}) by {idinfo['email']}")
    return {**audit, **write_up(audit)}
