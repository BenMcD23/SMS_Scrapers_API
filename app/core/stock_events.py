"""Append-only stock removal/return log carried by uniform and badge order items.

Both stores.py and badges.py record the same thing — "this order item was taken
off the shelf / put back, by whom, when" — so the shape lives here rather than
being spelled out twice. Events are stored as a JSON array on the order item;
the *last* event decides whether the item is currently off the shelf, and the
whole list is shown to the QM as history.

Events carry extra location keys (which stock row / badge cell it came from) so
a return can put the item back where it was taken from; those are internal and
`public_events` strips them before the list goes to the UI.
"""

import json
import uuid
from datetime import datetime

REMOVED = "removed"
RETURNED = "returned"


def events_list(raw: str | None) -> list:
    return json.loads(raw) if raw and raw.strip().startswith("[") else []


def is_removed(events: list) -> bool:
    """True when the item is currently off the shelf for this order."""
    return bool(events) and events[-1].get("action") == REMOVED


def last_removal(events: list) -> dict | None:
    for event in reversed(events):
        if event.get("action") == REMOVED:
            return event
    return None


def new_event(action: str, by: str | None, **extra) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "action": action,
        "timestamp": datetime.now().isoformat(),
        "by": by or None,
        **extra,
    }


def dump(events: list) -> str:
    return json.dumps(events)


def public_events(raw: str | None) -> list:
    return [
        {"id": e.get("id"), "action": e.get("action"), "timestamp": e.get("timestamp"), "by": e.get("by")}
        for e in events_list(raw)
    ]
