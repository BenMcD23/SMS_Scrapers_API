"""Google Calendar writes for the shared "NCO Holidays" calendar.

The same service account that sends mail and reads the directory acts as
IMPERSONATE_EMAIL here, so the calendar must be shared with IMPERSONATE_EMAIL
("Make changes to events") — sharing with the service account itself has no
effect while with_subject is in play. Missing that share reads as a 404, not a
403.

Nothing in here raises: a Calendar outage must never lose a holiday the NCO has
already booked, so every function reports success as a return value and the
caller records what happened.
"""

from datetime import datetime, timedelta

from googleapiclient.discovery import build as google_build

from core.config import (
    IMPERSONATE_EMAIL, NCO_HOLIDAY_CALENDAR_ID, SA_EMAIL, SA_PRIVATE_KEY,
)
from core.security import _service_account_creds

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Prefixes every event title, so the entries are still identifiable if someone
# copies one onto another calendar or looks at a combined agenda view.
EVENT_PREFIX = "NCO Holiday"


def calendar_configured() -> bool:
    return bool(NCO_HOLIDAY_CALENDAR_ID and SA_EMAIL and SA_PRIVATE_KEY)


def _service():
    creds = _service_account_creds(_SCOPES).with_subject(IMPERSONATE_EMAIL)
    return google_build("calendar", "v3", credentials=creds, cache_discovery=False)


def _event_body(name: str, email: str, date_from: datetime, date_to: datetime,
                reason: str) -> dict:
    """All-day event over the inclusive date_from..date_to range. Google's
    all-day `end.date` is exclusive, hence the extra day."""
    # Empty rather than absent, so editing a reason away clears it on the event.
    description = f"Reason: {reason}" if reason else ""
    return {
        "summary": f"{EVENT_PREFIX} — {name}",
        "description": description,
        "start": {"date": date_from.date().isoformat()},
        "end": {"date": (date_to.date() + timedelta(days=1)).isoformat()},
        "transparency": "transparent",  # doesn't mark anyone busy
    }


def create_holiday_event(name: str, email: str, date_from: datetime,
                         date_to: datetime, reason: str) -> str | None:
    """Push a booked holiday to the calendar. Returns the Google event id, or
    None if the calendar isn't configured or the call failed."""
    if not calendar_configured():
        print("[calendar] skipped: NCO_HOLIDAY_CALENDAR_ID or service account not configured")
        return None
    try:
        event = _service().events().insert(
            calendarId=NCO_HOLIDAY_CALENDAR_ID,
            body=_event_body(name, email, date_from, date_to, reason),
        ).execute()
        return event.get("id")
    except Exception as e:
        print(f"[calendar] create failed for {email}: {e}")
        return None


def update_holiday_event(event_id: str, name: str, email: str, date_from: datetime,
                         date_to: datetime, reason: str) -> bool:
    """Move an existing event onto a new range. False if there's nothing to
    update or the call failed — the caller treats that as "not on the calendar"
    and lets the existing retry recreate it."""
    if not event_id or not calendar_configured():
        return False
    try:
        _service().events().patch(
            calendarId=NCO_HOLIDAY_CALENDAR_ID,
            eventId=event_id,
            body=_event_body(name, email, date_from, date_to, reason),
        ).execute()
        return True
    except Exception as e:
        print(f"[calendar] update failed for {event_id}: {e}")
        return False


def delete_holiday_event(event_id: str) -> bool:
    """Remove a cancelled holiday from the calendar. An event that's already
    gone counts as success — the calendar is in the state we wanted either way."""
    if not event_id or not calendar_configured():
        return False
    try:
        _service().events().delete(
            calendarId=NCO_HOLIDAY_CALENDAR_ID, eventId=event_id,
        ).execute()
        return True
    except Exception as e:
        if getattr(getattr(e, "resp", None), "status", None) in (404, 410):
            return True
        print(f"[calendar] delete failed for {event_id}: {e}")
        return False
