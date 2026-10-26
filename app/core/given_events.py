"""Append-only "given" / "undone" log carried by uniform and badge order items.

Marking an item as given stamps `given_at` / `given_by`, which a later undo
clears. The log is what survives: who gave it and when, who undid it and when,
and (for uniform) the cadet's previous "last issued" record, so an undo can
put that back instead of leaving the wrong size on file. Same shape and
storage as `stock_events` — a JSON array on the item — so it reuses those
helpers; `public_events` strips the internal restore data before the UI sees it.
"""

from datetime import datetime

from core import stock_events
from core.stock_events import dump, events_list, public_events  # noqa: F401  (re-exported)

GIVEN = "given"
UNDONE = "undone"


def last_given(events: list) -> dict | None:
    """The most recent "given" event, or None for an item given before this log existed."""
    for event in reversed(events):
        if event.get("action") == GIVEN:
            return event
    return None


def new_event(action: str, by: str | None, **extra) -> dict:
    """A log entry stamped on the same clock as `given_at` (the app's UK local
    time), so the history line and the "Given …" stamp it explains always
    agree. Pass `timestamp=` to use an exact instant."""
    extra.setdefault("timestamp", datetime.now().isoformat())
    return stock_events.new_event(action, by, **extra)
