"""Training nights (staff only): what's running on a night, who's in each
session and how they're grouped, and which lessons cadets have missed.

Three parts, all built on the theory catalogue (core/theory_catalogue.py),
which is the list of lessons: the built-in syllabus plus subjects staff add,
each with a QR code per lesson that cadets scan to record they had it. So
attendance at a lesson is always the QR scan; nothing here ticks cadets off.

* **Programme links** say which lesson a programme activity is ("Map Reading 2"
  is lesson 2 of Map Reading). The model, or rules when it's down, suggests
  links for activities that have none; staff accept or change them. Accepting
  a suggested new subject adds it to the catalogue, QR codes and all.
* **Nights** are the sessions staff set up for a date, each with its own
  inputs (lesson, flights, who to plan with, everyone or only those who need
  it, how to group). They can be started from the night's programme. The
  groups are worked out on every request from who has checked in at the door
  and who has had the lesson (core/training_plan.py), so they follow the door
  and the QR scans as the night goes on.
* **Catch-ups** list, for each linked lesson the programme has run, the cadets
  in the flights it ran for who still have no record of it.
"""

import json
import logging
from collections import defaultdict
from datetime import date as Date
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from core import training_plan as tp
from core.attendance import PRESENT, attendance_state
from core.db import get_db
from core.llm import generate
from core.night_outlook import night_outlooks
from core.programme import CATEGORIES
from core.security import require_staff
from core.theory_catalogue import MAX_PARTS, add_codes, catalogue, new_key
from core.theory_progress import lessons_had
from database.models import (
    Cadet,
    CadetAttendance,
    CadetCheckIn,
    CadetTheoryLesson,
    ProgrammeLessonLink,
    ProgrammeNight,
    ProgrammeSession,
    TheorySection,
    TheorySubject,
    TrainingNightSession,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/training", tags=["training"])

# Where subjects made from the programme go on the QR codes page.
PROGRAMME_SECTION = "Squadron lessons"
CATCH_UP_DAYS = 365
UNLIKELY = 0.5
PARADE_NIGHT = "Parade Night"


def _clean(text: str) -> str:
    return " ".join(text.split())


def _who(idinfo: dict) -> str | None:
    return idinfo.get("email")


# ── programme links ───────────────────────────────────────────────────────────

def _link_out(link: ProgrammeLessonLink | None, cat) -> dict:
    if link is None:
        return {"state": "none", "subject_key": None, "subject_name": None, "new_subject": None, "part": None,
                "source": None}
    lesson = cat.by_key.get(link.subject_key) if link.subject_key else None
    if link.confirmed:
        state = "linked" if link.subject_key else "not_lesson"
    else:
        state = "suggested"
    return {
        "state": state,
        "subject_key": link.subject_key,
        "subject_name": lesson.name if lesson else None,
        "new_subject": link.new_subject,
        "part": link.part,
        "source": link.source,
        # A link to a subject since retired, or a lesson number it no longer
        # has, says so rather than quietly pointing nowhere.
        "broken": bool(link.subject_key) and (lesson is None or (link.part or 1) > lesson.parts),
    }


def _activities(db: Session) -> list[dict]:
    """Every distinct programme activity, with its commonest category, how
    many nights it ran and the last of them."""
    rows = (
        db.query(ProgrammeSession.activity, ProgrammeSession.category, ProgrammeNight.date, ProgrammeSession.flight)
        .join(ProgrammeNight, ProgrammeNight.id == ProgrammeSession.night_id)
        .all()
    )
    acts: dict[str, dict] = {}
    for activity, category, day, flight in rows:
        a = acts.setdefault(activity, {"categories": defaultdict(int), "nights": set(), "flights": set()})
        a["categories"][category] += 1
        a["nights"].add(day)
        a["flights"].add(flight)
    return [
        {
            "activity": name,
            "category": max(a["categories"], key=a["categories"].get),
            "nights": len(a["nights"]),
            "last": max(a["nights"]).isoformat(),
            "flights": sorted(a["flights"]),
        }
        for name, a in sorted(acts.items(), key=lambda kv: kv[0].lower())
    ]


@router.get("/links")
def programme_links(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every activity in the imported programme and the lesson it's linked to,
    suggested, or "not a lesson"."""
    cat = catalogue(db)
    links = {lk.activity: lk for lk in db.query(ProgrammeLessonLink)}
    return [{**a, **_link_out(links.get(a["activity"]), cat)} for a in _activities(db)]


LINK_SYSTEM_PROMPT = f"""You match an RAF Air Cadet squadron's parade-night programme activities to the
lessons in its training catalogue.
You get the catalogue (subjects, each taught over a number of numbered lessons) and a list of
activities from the programme, each with a category.
For each activity decide one of:
- it is lesson N of an existing subject: give "subject" (the subject's key) and "part" (N, at most the
  subject's number of lessons). "Map Reading 2" is lesson 2; an activity with no number is lesson 1.
- it is a lesson of a subject the catalogue doesn't have yet: give "new_subject" (a short title-case
  name with no lesson number, the same name for every activity that is part of it, e.g. "Map Reading"
  for "Map Reading 1" and "Map reading pt 2") and "part".
- it is not a lesson a cadet could miss and catch up on (drill, sport, socials, admin, flight time,
  inspections, band, general "classifications" nights, games): give neither.
Categories are: {", ".join(CATEGORIES)}.
Reply with JSON only: {{"links": [{{"i": <activity index>, "subject": <key or null>,
"new_subject": <name or null>, "part": <number or null>}}]}}"""


def suggest_by_ai(acts: list[dict], subjects: dict[str, tuple[str, int]]) -> dict[int, tp.LinkSuggestion]:
    """{index: suggestion} for the activities the model handled properly; the
    rest are left for the rules. A failed model chain is an empty result,
    never an error: suggestions are a convenience."""
    if not acts:
        return {}
    prompt = (
        "Catalogue:\n"
        + json.dumps([{"key": k, "name": n, "lessons": p} for k, (n, p) in subjects.items()], ensure_ascii=False)
        + "\nActivities:\n"
        + json.dumps([{"i": i, "name": a["activity"], "category": a["category"]} for i, a in enumerate(acts)],
                     ensure_ascii=False)
    )
    try:
        reply, _model = generate(prompt, LINK_SYSTEM_PROMPT, temperature=0.1, max_tokens=12000,
                                 groq_max_tokens=8000)
    except RuntimeError as e:
        logger.warning(f"programme links: every model failed, using rules: {e}")
        return {}
    start, end = reply.find("{"), reply.rfind("}")
    try:
        links = json.loads(reply[start:end + 1])["links"]
    except (ValueError, KeyError, TypeError):
        logger.warning("programme links: unreadable reply, using rules")
        return {}

    out: dict[int, tp.LinkSuggestion] = {}
    for item in links if isinstance(links, list) else []:
        try:
            i = int(item["i"])
            key = item.get("subject") or None
            new = _clean(str(item.get("new_subject") or ""))[:120] or None
            part = int(item["part"]) if item.get("part") is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= i < len(acts):
            continue
        if key is not None:
            # A key it made up, or a lesson the subject hasn't got, is the rules' to do.
            if key not in subjects or part is None or not 1 <= part <= subjects[key][1]:
                continue
            out[i] = tp.LinkSuggestion(key, None, part)
        elif new is not None:
            if part is None or not 1 <= part <= MAX_PARTS:
                continue
            out[i] = tp.LinkSuggestion(None, new, part)
        else:
            out[i] = tp.LinkSuggestion(None, None, None)
    return out


@router.post("/links/suggest")
async def suggest_links(
    use_ai: bool = Query(True), db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    """Suggest a lesson for every programme activity without a link yet.
    Suggestions wait for staff to accept them; confirmed links are never
    touched."""
    cat = catalogue(db)
    have = {a for (a,) in db.query(ProgrammeLessonLink.activity)}
    todo = [a for a in _activities(db) if a["activity"] not in have]
    subjects = {lesson.key: (lesson.name, lesson.parts) for lesson in cat.lessons}
    by_ai = await run_in_threadpool(suggest_by_ai, todo, subjects) if use_ai else {}
    now = datetime.now()
    counts = {"ai": 0, "rules": 0}
    for i, a in enumerate(todo):
        source = "ai" if i in by_ai else "rules"
        s = by_ai.get(i) or tp.suggest_by_rules(a["activity"], a["category"], subjects)
        db.add(ProgrammeLessonLink(activity=a["activity"], subject_key=s.subject_key, new_subject=s.new_subject,
                                   part=s.part, confirmed=False, source=source, updated_at=now,
                                   updated_by=_who(idinfo)))
        counts[source] += 1
    db.commit()
    return {"suggested": len(todo), **counts}


class LinkBody(BaseModel):
    activity: str = Field(min_length=1, max_length=200)
    # None means "not a lesson".
    subject_key: str | None = None
    part: int | None = Field(default=None, ge=1, le=MAX_PARTS)

    @model_validator(mode="after")
    def _both(self):
        if (self.subject_key is None) != (self.part is None):
            raise ValueError("Pick a subject and a lesson number, or neither for not a lesson")
        return self


def _check_lesson(cat, subject_key: str | None, part: int | None) -> None:
    if subject_key is None:
        return
    lesson = cat.by_key.get(subject_key)
    if lesson is None:
        raise HTTPException(status_code=404, detail="That subject isn't in the catalogue")
    if part > lesson.parts:
        raise HTTPException(status_code=400, detail=f"{lesson.name} has {lesson.parts} lesson"
                                                    f"{'' if lesson.parts == 1 else 's'}")


def _known_activity(db: Session, activity: str) -> None:
    if not db.query(ProgrammeSession.id).filter(ProgrammeSession.activity == activity).first():
        raise HTTPException(status_code=404, detail="That activity isn't in the programme")


@router.put("/links")
def set_link(body: LinkBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Link an activity to a lesson (or mark it not a lesson), confirmed."""
    _known_activity(db, body.activity)
    cat = catalogue(db)
    _check_lesson(cat, body.subject_key, body.part)
    link = db.query(ProgrammeLessonLink).filter_by(activity=body.activity).first()
    if link is None:
        link = ProgrammeLessonLink(activity=body.activity)
        db.add(link)
    link.subject_key, link.part, link.new_subject = body.subject_key, body.part, None
    link.confirmed, link.source = True, "staff"
    link.updated_at, link.updated_by = datetime.now(), _who(idinfo)
    db.commit()
    return {"activity": link.activity, **_link_out(link, cat)}


@router.delete("/links", status_code=204)
def clear_link(activity: str = Query(...), db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Forget an activity's link, so it can be suggested again."""
    link = db.query(ProgrammeLessonLink).filter_by(activity=activity).first()
    if link is None:
        raise HTTPException(status_code=404, detail="That activity has no link")
    db.delete(link)
    db.commit()


class AcceptBody(BaseModel):
    activities: list[str] = Field(min_length=1, max_length=1000)


def _programme_section(db: Session) -> str:
    """The section subjects from the programme go in, made the first time."""
    row = db.query(TheorySection).filter(func.lower(TheorySection.name) == PROGRAMME_SECTION.lower()).first()
    if row is None:
        row = TheorySection(name=PROGRAMME_SECTION, created_at=datetime.now())
        db.add(row)
        db.flush()
    return row.name


@router.post("/links/accept")
def accept_links(body: AcceptBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Accept the suggestions for these activities. A suggested new subject is
    added to the catalogue once, with as many lessons as the accepted
    activities need (an existing subject of the same name is used instead, and
    grown if it's one staff added). Anything already confirmed, or whose
    suggestion no longer fits the catalogue, is skipped and named."""
    links = {lk.activity: lk for lk in
             db.query(ProgrammeLessonLink).filter(ProgrammeLessonLink.activity.in_(set(body.activities)))}
    cat = catalogue(db)
    now, who = datetime.now(), _who(idinfo)
    skipped: list[dict] = []
    todo = []
    for activity in dict.fromkeys(body.activities):
        link = links.get(activity)
        if link is None or link.confirmed:
            skipped.append({"activity": activity, "reason": "Nothing to accept"})
        elif link.subject_key and (link.subject_key not in cat.by_key or link.part > cat.by_key[link.subject_key].parts):
            skipped.append({"activity": activity, "reason": "The suggested lesson isn't in the catalogue any more"})
        else:
            todo.append(link)

    # New subjects: one per name, sized for the highest lesson accepted.
    wanted: dict[str, int] = {}
    names: dict[str, str] = {}
    for link in todo:
        if link.new_subject:
            k = link.new_subject.lower()
            names.setdefault(k, link.new_subject)
            wanted[k] = max(wanted.get(k, 0), link.part or 1)
    made: dict[str, str] = {}
    created: list[str] = []
    for k, parts in wanted.items():
        existing = next((lesson for lesson in cat.lessons if lesson.name.lower() == k), None)
        if existing is not None:
            subject = db.query(TheorySubject).filter_by(key=existing.key).first()
            if subject is not None and subject.parts < parts:
                subject.parts = parts
                add_codes(db, subject.key, parts)
            elif subject is None and existing.parts < parts:
                # A built-in subject can't grow: those lessons stay unlinked.
                for link in todo:
                    if link.new_subject and link.new_subject.lower() == k and (link.part or 1) > existing.parts:
                        skipped.append({"activity": link.activity,
                                        "reason": f"{existing.name} only has {existing.parts} lessons"})
            made[k] = existing.key
            continue
        subject = TheorySubject(key=new_key(db, names[k]), name=names[k], category=_programme_section(db),
                                parts=parts, active=True, created_at=now, created_by=who)
        db.add(subject)
        db.flush()
        add_codes(db, subject.key, parts)
        made[k] = subject.key
        created.append(subject.name)

    skipped_names = {s["activity"] for s in skipped}
    accepted = 0
    for link in todo:
        if link.activity in skipped_names:
            continue
        if link.new_subject:
            link.subject_key, link.new_subject = made[link.new_subject.lower()], None
        link.confirmed, link.updated_at, link.updated_by = True, now, who
        accepted += 1
    db.commit()
    return {"accepted": accepted, "created_subjects": created, "skipped": skipped}


def confirmed_links(db: Session) -> dict[str, tuple[str, int]]:
    """{activity: (subject key, lesson)} for every confirmed lesson link."""
    return {a: (k, p) for a, k, p in db.query(
        ProgrammeLessonLink.activity, ProgrammeLessonLink.subject_key, ProgrammeLessonLink.part
    ).filter(ProgrammeLessonLink.confirmed.is_(True), ProgrammeLessonLink.subject_key.isnot(None))}


# ── nights ────────────────────────────────────────────────────────────────────

class SessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    period: int | None = Field(default=None, ge=1, le=6)
    subject_key: str | None = None
    part: int | None = Field(default=None, ge=1, le=MAX_PARTS)
    flights: list[str] = Field(default_factory=list, max_length=10)
    pool: str = "auto"
    include: str = "all"
    grouping: str = "one"
    group_count: int | None = Field(default=None, ge=1, le=20)
    max_group: int | None = Field(default=None, ge=2, le=60)
    instructor: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("Give the session a name")
        return v

    @field_validator("flights")
    @classmethod
    def _flights(cls, v: list[str]) -> list[str]:
        out = list(dict.fromkeys(f.strip().upper() for f in v if f.strip()))
        if any(len(f) > 10 for f in out):
            raise ValueError("That isn't a flight")
        return out

    @field_validator("instructor", "notes")
    @classmethod
    def _optional(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @model_validator(mode="after")
    def _choices(self):
        if self.pool not in tp.POOLS:
            raise ValueError(f"Who to plan with must be one of: {', '.join(tp.POOLS)}")
        if self.include not in tp.INCLUDES:
            raise ValueError(f"Include must be one of: {', '.join(tp.INCLUDES)}")
        if self.grouping not in tp.GROUPINGS:
            raise ValueError(f"Grouping must be one of: {', '.join(tp.GROUPINGS)}")
        if (self.subject_key is None) != (self.part is None):
            raise ValueError("Pick a subject and a lesson number, or neither")
        if self.subject_key is None and self.include == "need":
            raise ValueError("Only cadets who need it takes a lesson: pick one")
        if self.subject_key is None and self.grouping == "need":
            raise ValueError("Grouping by need takes a lesson: pick one")
        return self


def _apply(row: TrainingNightSession, body: SessionBody) -> None:
    for name in ("title", "period", "subject_key", "part", "flights", "pool", "include", "grouping",
                 "group_count", "max_group", "instructor", "notes"):
        setattr(row, name, getattr(body, name))
    row.updated_at = datetime.now()


def _sessions(db: Session, night: Date) -> list[TrainingNightSession]:
    rows = db.query(TrainingNightSession).filter(TrainingNightSession.date == night).all()
    # Periods in order with the free-standing ones last, then as added.
    return sorted(rows, key=lambda r: (r.period is None, r.period or 0, r.position, r.id))


def _programme_rows(db: Session, night: Date) -> list[ProgrammeSession]:
    row = db.query(ProgrammeNight).filter(ProgrammeNight.date == night).first()
    return list(row.sessions) if row else []


@router.get("/nights/{night}")
def training_night(night: Date, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """A night's programme, its sessions and, for each, who is in it and in
    which group, worked out now from the door and the QR scans."""
    cat = catalogue(db)
    links = {lk.activity: lk for lk in db.query(ProgrammeLessonLink)}
    programme = [
        {
            "period": s.period, "flight": s.flight, "activity": s.activity, "category": s.category,
            "instructor": s.instructor, "split": s.split,
            "link": _link_out(links.get(s.activity), cat),
        }
        for s in sorted(_programme_rows(db, night), key=lambda s: (s.period, s.flight, s.id))
    ]

    rows = _sessions(db, night)
    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    here = {cin for (cin,) in db.query(CadetCheckIn.cadet_id).filter(CadetCheckIn.date == night)}
    outlooks = night_outlooks(db, night, [c.cin for c in cadets])
    had = lessons_had(db)
    start = datetime.combine(night, datetime.min.time())
    scanned: set[tuple[int, str, int]] = {
        (cin, key, part) for cin, key, part in db.query(
            CadetTheoryLesson.cadet_id, CadetTheoryLesson.lesson_key, CadetTheoryLesson.part
        ).filter(CadetTheoryLesson.attended_at >= start, CadetTheoryLesson.attended_at < start + timedelta(days=1))
    }

    def absent(cin: int) -> bool:
        return any(f["code"] == "absence_booked" for f in outlooks[cin]["flags"])

    night_cadets = [
        tp.NightCadet(cin=c.cin, name=f"{c.first_name} {c.last_name}", rank=c.rank, flight=c.flight,
                      checked_in=c.cin in here, absent=absent(c.cin),
                      lessons={k: frozenset(v) for k, v in had.get(c.cin, {}).items()},
                      tonight=frozenset((k, p) for cin, k, p in scanned if cin == c.cin))
        for c in cadets
    ]
    sessions = [
        tp.NightSession(id=r.id, period=r.period, subject_key=r.subject_key if r.subject_key in cat.by_key else None,
                        part=r.part if r.subject_key in cat.by_key else None,
                        flights=tuple(r.flights or ()), pool=r.pool, include=r.include, grouping=r.grouping,
                        groups=r.group_count, max_group=r.max_group)
        for r in rows
    ]
    plans = tp.plan_night(sessions, night_cadets)
    by_cin = {c.cin: c for c in cadets}
    anyone_in = bool(here)

    def cadet_out(cin: int, row: TrainingNightSession, status: str | None) -> dict:
        c = by_cin[cin]
        likelihood = outlooks[cin]["likelihood"]
        return {
            "cin": c.cin, "first_name": c.first_name, "last_name": c.last_name, "rank": c.rank, "flight": c.flight,
            "checked_in": cin in here,
            "unlikely": cin not in here and likelihood is not None and likelihood < UNLIKELY,
            "status": status,
            "scanned": bool(row.subject_key) and (cin, row.subject_key, row.part) in scanned,
        }

    out = []
    for row, s, plan in zip(rows, sessions, plans, strict=True):
        lesson = cat.by_key.get(row.subject_key) if row.subject_key else None
        warnings = list(plan.warnings)
        if row.subject_key and (lesson is None or row.part > lesson.parts):
            warnings.append("Its lesson isn't in the catalogue any more: pick another.")
            lesson = None
        statuses = plan.statuses
        out.append({
            "id": row.id, "title": row.title, "period": row.period, "subject_key": row.subject_key,
            "part": row.part, "flights": row.flights or [], "pool": row.pool, "include": row.include,
            "grouping": row.grouping, "group_count": row.group_count, "max_group": row.max_group,
            "instructor": row.instructor, "notes": row.notes,
            "lesson": {"key": lesson.key, "name": lesson.name, "parts": lesson.parts,
                       "code": cat.codes.get((lesson.key, row.part))} if lesson else None,
            "planning_with": "checked_in" if tp.use_checked_in(s.pool, anyone_in) else "expected",
            "counts": {
                "cadets": len(plan.cins),
                "needs": sum(1 for v in statuses.values() if v == tp.NEEDS),
                "behind": sum(1 for v in statuses.values() if v == tp.BEHIND),
                "had": sum(1 for v in statuses.values() if v == tp.HAD),
                "scanned": sum(1 for cin in plan.cins if lesson and (cin, row.subject_key, row.part) in scanned),
            },
            "groups": [{"label": g.label, "cadets": [cadet_out(cin, row, statuses.get(cin)) for cin in g.cins]}
                       for g in plan.groups],
            "warnings": warnings,
        })

    return {
        "date": night.isoformat(),
        "checked_in": len(here),
        # The squadron's flights, for picking which a session is for.
        "flights": sorted({(c.flight or "").strip().upper() for c in cadets} - {""}),
        "programme": programme,
        "sessions": out,
    }


@router.post("/nights/{night}/sessions", status_code=201)
def add_session(night: Date, body: SessionBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    _check_lesson(catalogue(db), body.subject_key, body.part)
    last = db.query(func.max(TrainingNightSession.position)).filter(TrainingNightSession.date == night).scalar()
    now = datetime.now()
    row = TrainingNightSession(date=night, position=(last or 0) + 1, created_at=now, created_by=_who(idinfo))
    _apply(row, body)
    db.add(row)
    db.commit()
    return {"id": row.id}


def _session_or_404(db: Session, session_id: int) -> TrainingNightSession:
    row = db.get(TrainingNightSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That session isn't on any night")
    return row


@router.put("/sessions/{session_id}")
def edit_session(session_id: int, body: SessionBody, db: Session = Depends(get_db),
                 idinfo: dict = Depends(require_staff)):
    row = _session_or_404(db, session_id)
    _check_lesson(catalogue(db), body.subject_key, body.part)
    _apply(row, body)
    db.commit()
    return {"id": row.id}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    db.delete(_session_or_404(db, session_id))
    db.commit()


@router.post("/nights/{night}/from-programme", status_code=201)
def sessions_from_programme(night: Date, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Start a night from its programme: a session per activity per period,
    for the flights doing it. An activity linked to a lesson is grouped by
    need; where a flight is split between it and something else, it takes
    only those who still need it, so the rest fall to the other activity.
    Only for a night with no sessions yet, so nothing set up is overwritten."""
    if db.query(TrainingNightSession.id).filter(TrainingNightSession.date == night).first():
        raise HTTPException(status_code=409, detail="This night already has sessions. Add to them instead.")
    rows = _programme_rows(db, night)
    if not rows:
        raise HTTPException(status_code=404, detail="There's no programme imported for that night")
    cat = catalogue(db)
    links = {lk.activity: lk for lk in db.query(ProgrammeLessonLink)}

    merged: dict[tuple[int, str], dict] = {}
    for s in sorted(rows, key=lambda s: (s.period, s.id)):
        m = merged.setdefault((s.period, s.activity), {"flights": [], "split": False, "instructor": s.instructor})
        if s.flight not in m["flights"]:
            m["flights"].append(s.flight)
        m["split"] = m["split"] or s.split

    now, who = datetime.now(), _who(idinfo)
    for position, ((period, activity), m) in enumerate(merged.items(), start=1):
        link = links.get(activity)
        key = link.subject_key if link and link.subject_key in cat.by_key else None
        part = link.part if key and (link.part or 0) <= cat.by_key[key].parts else None
        key = key if part else None
        db.add(TrainingNightSession(
            date=night, position=position, period=period, title=activity, subject_key=key, part=part,
            flights=m["flights"], pool="auto", include="need" if key and m["split"] else "all",
            grouping="need" if key else "one", group_count=None, max_group=None,
            instructor=m["instructor"], notes=None, created_at=now, created_by=who, updated_at=now,
        ))
    db.commit()
    return {"sessions": len(merged)}


# ── catch-ups ─────────────────────────────────────────────────────────────────

@router.get("/catch-ups")
def catch_ups(
    days: int = Query(CATCH_UP_DAYS, ge=7, le=3 * 365),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """For each lesson the programme has run (through a confirmed link) in the
    last ``days``: when, for which flights, and who in those flights has no
    record of it yet. A cadet counts by their flight today, as the programme
    stats do. ``was_in`` says they were at the night it ran, so the scan was
    missed rather than the lesson."""
    today = Date.today()
    since = today - timedelta(days=days)
    links = confirmed_links(db)
    cat = catalogue(db)
    if not links:
        return []
    rows = (
        db.query(ProgrammeNight.date, ProgrammeSession.flight, ProgrammeSession.activity, ProgrammeSession.split)
        .join(ProgrammeNight, ProgrammeNight.id == ProgrammeSession.night_id)
        .filter(ProgrammeNight.date >= since, ProgrammeNight.date <= today,
                ProgrammeSession.activity.in_(list(links)))
        .all()
    )
    ran: dict[tuple[str, int], dict[Date, dict]] = defaultdict(dict)
    for day, flight, activity, split in rows:
        key, part = links[activity]
        if key not in cat.by_key or part > cat.by_key[key].parts:
            continue
        r = ran[(key, part)].setdefault(day, {"flights": set(), "split": False})
        r["flights"].add(flight.strip().upper())
        r["split"] = r["split"] or split
    if not ran:
        return []

    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    had = lessons_had(db)
    days_ran = {d for runs in ran.values() for d in runs}
    start = datetime.combine(min(days_ran), datetime.min.time())
    present: set[tuple[int, Date]] = {
        (cin, when.date()) for cin, when, status in db.query(
            CadetAttendance.cadet_id, CadetAttendance.date, CadetAttendance.status
        ).filter(CadetAttendance.register_type == PARADE_NIGHT, CadetAttendance.date >= start)
        if attendance_state(status) == PRESENT
    }
    present |= {(cin, d) for cin, d in db.query(CadetCheckIn.cadet_id, CadetCheckIn.date)
                .filter(CadetCheckIn.date >= min(days_ran))}

    order = {lesson.key: i for i, lesson in enumerate(cat.lessons)}
    out = []
    for (key, part), runs in sorted(ran.items(), key=lambda kv: (order.get(kv[0][0], 0), kv[0][1])):
        lesson = cat.by_key[key]
        missing = []
        for c in cadets:
            flight = (c.flight or "").strip().upper()
            when = [d for d, r in runs.items() if flight in r["flights"]]
            if not when or part in had.get(c.cin, {}).get(key, set()):
                continue
            missing.append({
                "cin": c.cin, "first_name": c.first_name, "last_name": c.last_name, "rank": c.rank,
                "flight": c.flight, "was_in": any((c.cin, d) in present for d in when),
            })
        out.append({
            "subject_key": key, "name": lesson.name, "part": part, "parts": lesson.parts,
            "code": cat.codes.get((key, part)),
            "ran": [{"date": d.isoformat(), "flights": sorted(r["flights"]), "split": r["split"]}
                    for d, r in sorted(runs.items())],
            "missing": missing,
        })
    return out
