"""The session task bank's rules: which task a leadership sheet was for, and who
has led which task.

Leadership sheets saved before the bank existed only carry the free-text
``exercise_no`` / ``exercise_name`` the assessor typed. Rather than rewrite
them in a one-off backfill, a sheet without a ``task_id`` is matched to the
bank when it's read: on its number first, then its name, ignoring case and
spacing. A sheet that matches no task, or more than one, stays unlinked and is
listed for staff to link by hand (which writes ``task_id`` for good). Adding a
task to the bank later picks up the old sheets that were for it.

The matching is kept free of the database so it can be tested on its own.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

# What a task can be for. Only leadership is assessed against the bank so far;
# the others are stands the planner rotates groups through.
LEADERSHIP = "leadership"
SESSION_KINDS = (LEADERSHIP, "radio", "first_aid", "fieldcraft", "drill", "sports", "general")

LEADERSHIP_ASSESSMENT = "Blue Leadership"


@dataclass(frozen=True)
class TaskKey:
    """The parts of a task a sheet can be matched on."""
    id: int
    number: str | None
    name: str


def _norm(text) -> str:
    """'  Spider's  Web ' and "spiders web" compare equal; so do '07' and '7'."""
    s = re.sub(r"['’]", "", str(text or "").lower())
    s = re.sub(r"[\W_]+", " ", s).strip()
    return " ".join(w.lstrip("0") or "0" if w.isdigit() else w for w in s.split())


def match_task(fields: dict | None, tasks: Iterable[TaskKey]) -> int | None:
    """The bank task a leadership sheet was for, or None if it can't be told.

    A stored ``task_id`` always wins, even if that task has since been retired.
    Otherwise a number that picks out exactly one task decides it; failing that,
    a name that does. Two tasks sharing a number and a name stay unmatched
    rather than guessing."""
    fields = fields or {}
    if fields.get("task_id") is not None:
        try:
            return int(fields["task_id"])
        except (TypeError, ValueError):
            return None
    tasks = list(tasks)
    number, name = _norm(fields.get("exercise_no")), _norm(fields.get("exercise_name"))
    if number:
        hits = [t.id for t in tasks if t.number and _norm(t.number) == number]
        if len(hits) == 1:
            return hits[0]
    if name:
        hits = [t.id for t in tasks if _norm(t.name) == name]
        if len(hits) == 1:
            return hits[0]
    return None


@dataclass(frozen=True)
class Lead:
    """One time a cadet led a task, from one assessment sheet."""
    sheet_id: int
    task_id: int
    passed: bool
    date: str


def leads_by_cadet(sheets: Iterable[tuple[int, int, dict]], tasks: Iterable[TaskKey]) -> dict[int, list[Lead]]:
    """Every matched lead per cadet, from ``(sheet_id, cin, fields)`` rows.

    A failed sheet counts as having led the task: the form itself says a cadet
    who fails is re-assessed "on another exercise"."""
    tasks = list(tasks)
    out: dict[int, list[Lead]] = defaultdict(list)
    for sheet_id, cin, fields in sheets:
        task_id = match_task(fields, tasks)
        if task_id is None:
            continue
        out[cin].append(Lead(sheet_id, task_id, bool((fields or {}).get("passed")),
                             str((fields or {}).get("date_iso") or (fields or {}).get("date") or "")))
    return out
