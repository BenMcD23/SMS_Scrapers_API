"""Inspection marking sheet — scraped absences for a parade date, sheet
submission with AWOL detection (marked absent but no absence log), and
per-cadet inspection history with score/attendance trends plus an optional
Groq-powered analysis of recurring uniform faults."""

import io
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from database.models import Cadet, CadetAbsence, InspectionSheet
from core.config import GROQ_API_KEY
from core.db import get_db
from core.security import require_staff

router = APIRouter()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Bad date, expected YYYY-MM-DD")


def _absent_cins_on(db: Session, date: datetime) -> set[int]:
    rows = (
        db.query(CadetAbsence.cadet_id)
        .filter(and_(CadetAbsence.date_from <= date, CadetAbsence.date_to >= date))
        .all()
    )
    return {r[0] for r in rows}


@router.get("/absences")
async def absences_on_date(
    date: str | None = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Absences covering the given date (defaults to today). One entry per
    absent cadet — the inspection page crosses these out."""
    d = _parse_date(date) if date else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.query(CadetAbsence)
        .filter(and_(CadetAbsence.date_from <= d, CadetAbsence.date_to >= d))
        .all()
    )
    return [
        {
            "cin":       a.cadet_id,
            "date_from": a.date_from.date().isoformat(),
            "date_to":   a.date_to.date().isoformat(),
            "reason":    a.reason,
        }
        for a in rows
    ]


class InspectionMark(BaseModel):
    cin: int
    score: float | None = None
    absent: bool = False
    comments: list[dict] = []


class InspectionSubmit(BaseModel):
    date: str
    marks: list[InspectionMark]


# Penalty applied to a flight's total for every AWOL cadet (marked absent with
# no matching absence log scraped from SMS).
AWOL_PENALTY = 5


def _recompute_awol(marks: list[dict], absent_with_log: set[int]) -> set[int]:
    """Cins that are AWOL: marked absent with no matching absence log scraped
    from SMS for the date. Recomputed over the whole sheet since the absence log
    is per-date and applies to every mark."""
    return {
        m["cin"] for m in marks
        if m.get("absent") and m["cin"] not in absent_with_log
    }


def _flight_scores(marks: list[dict], awol: set[int],
                   flight_by_cin: dict[int, str | None]) -> dict[str, dict]:
    """Per-flight totals: present cadets' scores summed, minus AWOL_PENALTY for
    each AWOL cadet. Absent-with-log cadets contribute nothing. ``marks`` are
    plain dicts (``model_dump()`` output or stored marks)."""
    scores: dict[str, dict] = {}
    for m in marks:
        cin = m["cin"]
        flight = flight_by_cin.get(cin) or "Unassigned"
        fs = scores.setdefault(
            flight, {"total": 0.0, "present_count": 0, "awol_count": 0}
        )
        if cin in awol:
            fs["total"] -= AWOL_PENALTY
            fs["awol_count"] += 1
        elif not m.get("absent"):
            if m.get("score") is not None:
                fs["total"] += m["score"]
            fs["present_count"] += 1
    for fs in scores.values():
        fs["total"] = round(fs["total"], 2)
    return scores


@router.post("/inspections")
async def submit_inspection(
    body: InspectionSubmit,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Persist a submitted sheet and flag AWOLs — cadets marked absent with no
    matching absence log for the date.

    Submissions are merged by date: if a sheet already exists for the parade date
    (e.g. another staff member inspected a different flight), the incoming marks
    are appended to it. When two submitters overlap on the same cadet the incoming
    (latest) submission wins. AWOL flags and per-flight scores are recomputed over
    the merged set."""
    d = _parse_date(body.date)
    absent_with_log = _absent_cins_on(db, d)

    existing = (
        db.query(InspectionSheet)
        .filter(InspectionSheet.date == d)
        .order_by(InspectionSheet.id)
        .first()
    )

    # Merge existing marks with the incoming ones, keyed by cin — incoming wins.
    merged: dict[int, dict] = {}
    if existing:
        for m in (existing.data or {}).get("marks", []):
            if m.get("cin") is not None:
                merged[m["cin"]] = dict(m)
    for m in body.marks:
        merged[m.cin] = m.model_dump()

    marks = list(merged.values())
    awol = _recompute_awol(marks, absent_with_log)
    for m in marks:
        m["awol"] = m["cin"] in awol

    flight_by_cin = dict(
        db.query(Cadet.cin, Cadet.flight)
        .filter(Cadet.cin.in_(list(merged.keys())))
        .all()
    )
    flight_scores = _flight_scores(marks, awol, flight_by_cin)
    data = {"marks": marks, "flight_scores": flight_scores}

    email = idinfo.get("email")
    if existing:
        # Preserve the roster of contributing inspectors, most-recent last.
        submitters = [s.strip() for s in (existing.submitted_by or "").split(",") if s.strip()]
        if email and email in submitters:
            submitters.remove(email)
        if email:
            submitters.append(email)
        existing.submitted_by = ", ".join(submitters) or None
        existing.submitted_at = datetime.now()
        existing.data = data  # reassign so SQLAlchemy detects the JSON change
        sheet = existing
    else:
        sheet = InspectionSheet(
            date=d,
            submitted_by=email,
            submitted_at=datetime.now(),
            data=data,
        )
        db.add(sheet)
    db.commit()

    # Report only the AWOLs from this submission, so the toast reflects the cadets
    # this staff member just flagged rather than the whole sheet.
    incoming_awol = sorted(
        m.cin for m in body.marks if m.absent and m.cin not in absent_with_log
    )
    return {"id": sheet.id, "awol": incoming_awol, "flight_scores": flight_scores}


# ── History & trends ──────────────────────────────────────────────────────────

def _ordinal(n: int) -> str:
    """Rank label with an ordinal suffix, mirroring the squadron spreadsheet's
    formula exactly: 11–19 always take "th", otherwise the last digit decides
    (1→st, 2→nd, 3→rd, else th)."""
    if 10 < n < 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _competition_ranks(values: list[float]) -> list[int]:
    """Rank ``values`` highest-first with standard competition ranking — ties
    share the lowest rank and the next distinct value skips accordingly
    (e.g. 1, 1, 3). Returns a rank per input position."""
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    ranks = [0] * len(values)
    prev_val: float | None = None
    prev_rank = 0
    for pos, i in enumerate(order, start=1):
        v = values[i]
        if prev_val is not None and v == prev_val:
            ranks[i] = prev_rank
        else:
            ranks[i] = pos
            prev_rank = pos
            prev_val = v
    return ranks


def _split_comments(comments: list[dict]) -> tuple[list[dict], list[dict]]:
    faults, positives = [], []
    for x in comments or []:
        entry = {"region": x.get("region"), "text": x.get("text")}
        (faults if x.get("type") == "fault" else positives).append(entry)
    return faults, positives


@router.get("/inspections/history")
async def inspection_history(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Per-cadet inspection history across every submitted sheet, with score and
    attendance averages plus squadron rankings.

    Attendance average is present sheets over the total number of inspections;
    overall = attendance fraction × score average (the squadron's own metric).
    All three columns are competition-ranked highest-first."""
    sheets = db.query(InspectionSheet).order_by(InspectionSheet.date).all()
    cadets = (
        db.query(Cadet)
        .filter(Cadet.banned.is_(False))
        .order_by(Cadet.last_name, Cadet.first_name)
        .all()
    )
    total = len(sheets)
    dates = [s.date.date().isoformat() for s in sheets]

    timelines: dict[int, list[dict]] = {c.cin: [] for c in cadets}
    for s in sheets:
        d = s.date.date().isoformat()
        by_cin = {
            m.get("cin"): m
            for m in (s.data or {}).get("marks", [])
            if m.get("cin") is not None
        }
        for c in cadets:
            m = by_cin.get(c.cin)
            if m is None:
                continue  # cadet wasn't recorded on this sheet
            faults, positives = _split_comments(m.get("comments") or [])
            timelines[c.cin].append({
                "date":      d,
                "score":     m.get("score"),
                "absent":    bool(m.get("absent")),
                "awol":      bool(m.get("awol")),
                "faults":    faults,
                "positives": positives,
            })

    rows = []
    for c in cadets:
        tl = timelines[c.cin]
        present = [e for e in tl if not e["absent"]]
        scored = [e["score"] for e in present if e["score"] is not None]
        attendance = (len(present) / total) if total else 0.0
        score_avg = (sum(scored) / len(scored)) if scored else 0.0
        overall = attendance * score_avg
        rows.append({
            "cin":            c.cin,
            "first_name":     c.first_name,
            "last_name":      c.last_name,
            "rank":           c.rank,
            "flight":         c.flight,
            "timeline":       tl,
            "present_count":  len(present),
            "attendance_avg": round(attendance * 100, 2),
            "score_avg":      round(score_avg, 2),
            "overall":        round(overall, 2),
        })

    att_ranks = _competition_ranks([r["attendance_avg"] for r in rows])
    score_ranks = _competition_ranks([r["score_avg"] for r in rows])
    overall_ranks = _competition_ranks([r["overall"] for r in rows])
    for r, ar, sr, orank in zip(rows, att_ranks, score_ranks, overall_ranks):
        r["attendance_rank"] = ar
        r["score_rank"] = sr
        r["overall_rank"] = orank
        r["overall_rank_label"] = _ordinal(orank)

    return {"inspection_count": total, "dates": dates, "cadets": rows}


# ── Per-date sheet browsing & PDF export ──────────────────────────────────────

def _grouped_sheet(db: Session, sheet: InspectionSheet) -> list[dict]:
    """Group a sheet's marks by flight, resolving cadet names, ready for display
    or PDF. Each flight carries its own present count, score total and average."""
    marks = (sheet.data or {}).get("marks", [])
    cins = [m.get("cin") for m in marks if m.get("cin") is not None]
    cadet_by_cin = {
        c.cin: c for c in db.query(Cadet).filter(Cadet.cin.in_(cins)).all()
    }

    flights: dict[str, dict] = {}
    for m in marks:
        cin = m.get("cin")
        cadet = cadet_by_cin.get(cin)
        flight = (cadet.flight if cadet else None) or "Unassigned"
        faults, positives = _split_comments(m.get("comments") or [])
        fl = flights.setdefault(
            flight, {"flight": flight, "cadets": [], "present": 0, "total": 0.0}
        )
        absent = bool(m.get("absent"))
        score = m.get("score")
        if not absent:
            fl["present"] += 1
            if score is not None:
                fl["total"] += score
        fl["cadets"].append({
            "cin":        cin,
            "first_name": cadet.first_name if cadet else "Unknown",
            "last_name":  cadet.last_name if cadet else str(cin),
            "rank":       cadet.rank if cadet else None,
            "flight":     flight,
            "score":      score,
            "absent":     absent,
            "awol":       bool(m.get("awol")),
            "faults":     faults,
            "positives":  positives,
        })

    out = []
    for fl in flights.values():
        fl["cadets"].sort(key=lambda c: (c["last_name"], c["first_name"]))
        fl["total"] = round(fl["total"], 2)
        fl["average"] = round(fl["total"] / fl["present"], 2) if fl["present"] else 0.0
        out.append(fl)
    out.sort(key=lambda f: _flight_order(f["flight"]))
    return out


def _flight_order(flight: str) -> tuple:
    order = {"NCO": 0, "A": 1, "B": 2, "C": 3}
    return (order.get(flight, 98), flight)


@router.get("/inspections/sheets")
async def list_sheets(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Submitted inspection sheets, newest first — for the date picker."""
    sheets = db.query(InspectionSheet).order_by(InspectionSheet.date.desc()).all()
    out = []
    for s in sheets:
        marks = (s.data or {}).get("marks", [])
        present = sum(1 for m in marks if not m.get("absent"))
        out.append({
            "id":           s.id,
            "date":         s.date.date().isoformat(),
            "submitted_by": s.submitted_by,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "cadet_count":  len(marks),
            "present":      present,
        })
    return out


@router.get("/inspections/sheets/{sheet_id}")
async def sheet_detail(
    sheet_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Full sheet grouped by flight, with cadet names and comments resolved."""
    sheet = db.query(InspectionSheet).filter(InspectionSheet.id == sheet_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Inspection not found")
    return {
        "id":           sheet.id,
        "date":         sheet.date.date().isoformat(),
        "submitted_by": sheet.submitted_by,
        "submitted_at": sheet.submitted_at.isoformat() if sheet.submitted_at else None,
        "flights":      _grouped_sheet(db, sheet),
    }


@router.delete("/inspections/sheets/{sheet_id}")
async def delete_sheet(
    sheet_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Permanently delete a submitted inspection sheet."""
    sheet = db.query(InspectionSheet).filter(InspectionSheet.id == sheet_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Inspection not found")
    db.delete(sheet)
    db.commit()
    return {"deleted": sheet_id}


@router.get("/inspections/sheets/{sheet_id}/pdf")
async def sheet_pdf(
    sheet_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Export a whole inspection (all flights) as a printable PDF."""
    from fastapi.responses import StreamingResponse
    from assessment_builders.inspection_pdf import build_inspection_pdf

    sheet = db.query(InspectionSheet).filter(InspectionSheet.id == sheet_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Inspection not found")

    date_str = sheet.date.date().isoformat()
    pdf = build_inspection_pdf(date_str, _grouped_sheet(db, sheet))
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="inspection-{date_str}.pdf"'
        },
    )


# ── AI analysis ───────────────────────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = (
    "You are a senior air cadet uniform inspecting officer. You review a cadet's "
    "inspection history and give concise, constructive feedback a staff member "
    "can act on. British English. Never invent faults that aren't in the data."
)

ANALYSIS_PROMPT_TEMPLATE = """Analyse this cadet's uniform inspection history and identify TRENDS.

Cadet: {name}
Inspections recorded: {count}
Score trend (oldest to newest, "-" = absent): {scores}
Average score: {score_avg} / 10

Recurring faults by uniform area (most frequent first):
{fault_summary}

Positive notes logged:
{positive_summary}

Write a short report with these sections, using markdown headings:
### Overall
One or two sentences on whether they are improving, declining or steady.
### Recurring issues
The uniform areas that keep coming up, with the specific fault. If a fault
appears across multiple inspections, say so plainly — that's the key insight.
### What to focus on next
2–3 specific, practical actions for this cadet.

Keep it under 180 words. If there is very little data, say so and keep it brief."""


class AnalyseRequest(BaseModel):
    cin: int


def _call_groq(system: str, user: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured on the server")
    resp = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            "max_tokens": 1500,
            "reasoning_effort": "low",
        },
        timeout=60,
    )
    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"Groq API error: {resp.text[:300]}")
    return data["choices"][0]["message"]["content"].strip()


@router.post("/inspections/analyse")
async def analyse_cadet(
    body: AnalyseRequest,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Groq-powered reasoning over a single cadet's inspection history, focused
    on recurring uniform faults and score trends."""
    cadet = db.query(Cadet).filter(Cadet.cin == body.cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail="Cadet not found")

    sheets = db.query(InspectionSheet).order_by(InspectionSheet.date).all()

    scores: list[str] = []
    fault_counts: dict[tuple[str, str], int] = {}
    positive_notes: list[str] = []
    recorded = 0
    for s in sheets:
        mark = next(
            (m for m in (s.data or {}).get("marks", []) if m.get("cin") == body.cin),
            None,
        )
        if mark is None:
            continue
        recorded += 1
        if mark.get("absent"):
            scores.append("-")
            continue
        scores.append(str(mark.get("score")) if mark.get("score") is not None else "?")
        faults, positives = _split_comments(mark.get("comments") or [])
        for f in faults:
            key = (f["region"] or "General", (f["text"] or "").strip().lower())
            fault_counts[key] = fault_counts.get(key, 0) + 1
        for p in positives:
            positive_notes.append(f"{p['region']}: {p['text']}")

    if recorded == 0:
        raise HTTPException(status_code=404, detail="No inspection history for this cadet")

    numeric = [float(x) for x in scores if x not in ("-", "?")]
    score_avg = round(sum(numeric) / len(numeric), 2) if numeric else 0.0

    fault_summary = "\n".join(
        f"- {region} — \"{text}\" (×{n})"
        for (region, text), n in sorted(fault_counts.items(), key=lambda kv: -kv[1])
    ) or "- None logged"
    positive_summary = "\n".join(f"- {p}" for p in positive_notes[:12]) or "- None logged"

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        name=f"{cadet.rank + ' ' if cadet.rank else ''}{cadet.first_name} {cadet.last_name}",
        count=recorded,
        scores=", ".join(scores),
        score_avg=score_avg,
        fault_summary=fault_summary,
        positive_summary=positive_summary,
    )

    try:
        analysis = _call_groq(ANALYSIS_SYSTEM_PROMPT, prompt)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"cin": body.cin, "model": GROQ_MODEL, "analysis": analysis}
