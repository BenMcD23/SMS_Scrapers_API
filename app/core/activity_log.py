"""The rules behind a cadet's DofE activity log: weeks, progress and finishing.

A section (Skills, Volunteering, Physical) runs for 3, 6, 12 or 18 months and
needs at least an hour a week. Weeks run Monday to Sunday, the same weeks on
the cadet's page, the staff dashboard and the PDF, and the week containing the
start date is week one. A week counts once its entries add up to an hour, so a
cadet who missed a few can catch up by logging them late, but never early: an
entry can't be dated after today, so twelve weeks can't be done in fewer.

A month is four weeks, as on the paper sheet's own tally (3 months = 12 weeks).
"""

from collections import defaultdict
from datetime import date, timedelta

SECTIONS = {"skills": "Skills", "volunteering": "Volunteering", "physical": "Physical"}
DURATION_WEEKS = {3: 12, 6: 24, 12: 48, 18: 72}
WEEKLY_MINUTES = 60


def week_start(d: date) -> date:
    """The Monday of ``d``'s week."""
    return d - timedelta(days=d.weekday())


def add_months(d: date, months: int) -> date:
    """``d`` plus whole calendar months, clamped to the end of a short month —
    the sheet's "End Date" (31 Aug + 6 months is 28 Feb, not an error)."""
    month = d.month - 1 + months
    year, month = d.year + month // 12, month % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def minutes_by_week(entries) -> dict[date, int]:
    totals: dict[date, int] = defaultdict(int)
    for e in entries:
        totals[week_start(e.date)] += e.minutes
    return totals


def progress(log, today: date) -> dict:
    """Where a log is up to. ``weeks`` lists every week from the start to this
    one, so the page can show the gaps; ``missed`` is the past weeks still under
    an hour — the ones a catch-up fills in."""
    totals = minutes_by_week(log.entries)
    first, current = week_start(log.start_date), week_start(today)
    weeks = []
    w = first
    while w <= current:
        minutes = totals.get(w, 0)
        weeks.append({"week_start": w.isoformat(), "minutes": minutes, "done": minutes >= WEEKLY_MINUTES})
        w += timedelta(weeks=1)
    needed = DURATION_WEEKS[log.duration_months]
    done = sum(1 for w, m in totals.items() if w >= first and m >= WEEKLY_MINUTES)
    this_week = totals.get(current, 0) if current >= first else 0
    return {
        "weeks_done": done,
        "weeks_needed": needed,
        "complete": done >= needed,
        "this_week_minutes": this_week,
        "started": today >= log.start_date,
        "weeks": weeks,
        "missed": [w["week_start"] for w in weeks[:-1] if not w["done"]] if current >= first else [],
        "end_date": add_months(log.start_date, log.duration_months).isoformat(),
    }
