"""When a lapsed cadet enters the leaving process, and where they are in it.

Pure date arithmetic, kept out of the router so it can be checked without a
database (see routers/test_leaving.py).
"""

from datetime import datetime

# "Last parade was 4 weeks from the current parade night."
LAPSE_DAYS = 28
# How long a cadet gets to reply before staff start the discharge.
RESPONSE_DAYS = 14

FLAGGED = "flagged"              # lapsed, no email sent yet
WAITING = "waiting"              # email sent, still inside the two weeks
START_LEAVING = "start_leaving"  # two weeks elapsed with no return


def is_lapsed(last_attended: datetime | None, current_parade: datetime | None) -> bool:
    """True when the cadet's last parade is at least four weeks before the most
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
