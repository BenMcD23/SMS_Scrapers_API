"""When a lapsed cadet enters the leaving process, and where they are in it.

Pure date arithmetic, kept out of the router so it can be checked without a
database (see routers/test_leaving.py).
"""

import re
from datetime import datetime, timedelta

from core.emailer import EMAIL_RE

# The window the attendance rate is measured over: the 8 weeks (two months) up
# to and including the most recent parade night.
LAPSE_DAYS = 56
# A cadet who was at fewer than this percentage of the parade nights held in the
# window is lapsed. The frontend only displays it (lib/leaving.ts).
LAPSE_BELOW_PCT = 40
# How long a cadet gets to reply before staff start the discharge.
RESPONSE_DAYS = 14

FLAGGED = "flagged"              # lapsed, parents not yet notified
WAITING = "waiting"              # parents notified, still inside the two weeks
START_LEAVING = "start_leaving"  # two weeks elapsed with no return


def attendance_in_window(
    nights: list[datetime],
    attended: set[datetime],
    first_seen: datetime | None,
    current_parade: datetime | None,
) -> tuple[int, int]:
    """(parade nights attended, parade nights they could have attended) over the
    ``LAPSE_DAYS`` up to ``current_parade``.

    ``nights`` is every parade night the squadron held; ``attended`` the dates
    this cadet was counted at (authorised absences count, as someone excused for
    exams hasn't drifted away). Nights before ``first_seen`` (their first
    register of any kind) are left out, so a cadet who joined three weeks ago
    isn't marked down for the five weeks before they existed. A cadet with no
    register at all gets ``(0, 0)``.
    """
    if first_seen is None or current_parade is None:
        return 0, 0
    start = current_parade - timedelta(days=LAPSE_DAYS)
    possible = [n for n in nights if start < n <= current_parade and n >= first_seen]
    return sum(1 for n in possible if n in attended), len(possible)


def attendance_pct(attended: int, possible: int) -> int | None:
    """Whole-number percentage, or None when there was nothing to attend."""
    return round(100 * attended / possible) if possible else None


def is_lapsed(attended: int, possible: int) -> bool:
    """True when the cadet was at under ``LAPSE_BELOW_PCT``% of the parade nights
    in the window. Nothing to measure (no register at all, or a brand new cadet
    with nothing scraped yet: ``possible == 0``) is never lapsed — guessing would
    flag people wrongly. Integer arithmetic so exactly 40% is not lapsed.
    """
    return possible > 0 and attended * 100 < LAPSE_BELOW_PCT * possible


def gap_days(last_attended: datetime | None, current_parade: datetime | None) -> int | None:
    if last_attended is None or current_parade is None:
        return None
    return max((current_parade - last_attended).days, 0)


def leaving_status(sent_at: datetime | None, now: datetime) -> tuple[str, int | None]:
    """(status, days_left). ``days_left`` is only meaningful while WAITING.

    Derived from ``sent_at`` on every read rather than stored, so the countdown
    is always right without anything having to tick it over.
    """
    if sent_at is None:
        return FLAGGED, None
    elapsed = (now - sent_at).days
    if elapsed < RESPONSE_DAYS:
        return WAITING, RESPONSE_DAYS - elapsed
    return START_LEAVING, 0


def parse_emails(raw: str | None) -> list[str]:
    """Split a pasted blob of addresses (``;``, commas, spaces or newlines) into
    a de-duplicated list, keeping first-seen order. Raises ``ValueError`` naming
    the first thing that isn't an address, so a typo is caught on save rather
    than when it silently bounces from the OC's BCC. Duplicates are matched
    case-insensitively — siblings share a parent.
    """
    seen: dict[str, str] = {}
    for part in re.split(r"[;,\s]+", raw or ""):
        if not part:
            continue
        if not EMAIL_RE.match(part):
            raise ValueError(f"'{part}' is not an email address")
        seen.setdefault(part.lower(), part)
    return list(seen.values())
