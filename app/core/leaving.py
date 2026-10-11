"""When a lapsed cadet enters the leaving process, and where they are in it.

Pure date arithmetic, kept out of the router so it can be checked without a
database (see routers/test_leaving.py).
"""

import re
from datetime import datetime

from core.emailer import EMAIL_RE

# "Last parade was 8 weeks (two months) before the current parade night."
LAPSE_DAYS = 56
# How long a cadet gets to reply before staff start the discharge.
RESPONSE_DAYS = 14

FLAGGED = "flagged"              # lapsed, parents not yet notified
WAITING = "waiting"              # parents notified, still inside the two weeks
START_LEAVING = "start_leaving"  # two weeks elapsed with no return


def is_lapsed(last_attended: datetime | None, current_parade: datetime | None) -> bool:
    """True when the cadet's last parade is at least eight weeks before the most
    recent parade night. Unknown either way (no register at all, or a brand new
    cadet with nothing scraped yet) is never lapsed — we can't measure a gap we
    have no endpoints for, and guessing would flag people wrongly.
    """
    if last_attended is None or current_parade is None:
        return False
    return (current_parade - last_attended).days >= LAPSE_DAYS


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
