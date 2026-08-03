"""Short-lived tickets for EventSource streams.

EventSource can't set an Authorization header, so the SSE endpoints used to
accept the raw Google ID token as a query parameter. Query strings end up in
proxy and access logs, browser history and Referer headers — and with the API
behind a Funnel proxy that is somewhere a credential valid for up to an hour
cannot be pulled back from.

A ticket is minted from a normally authenticated request, is single-use, lasts
30 seconds, and carries nothing beyond the email it was issued to. Leaking one
costs a stream connection rather than an account.

Held in process memory, like the scraper run states and the verified-token
cache alongside it — the API runs as a single uvicorn process.
"""

import secrets
import threading
import time

TTL_SECONDS = 30

_tickets: dict[str, tuple[str, float]] = {}   # ticket -> (email, expires_at)
_lock = threading.Lock()


def issue_ticket(email: str) -> str:
    now = time.time()
    ticket = secrets.token_urlsafe(32)
    with _lock:
        # Prune on write; there's no background sweep and the dict is tiny.
        for t in [t for t, (_, exp) in _tickets.items() if exp <= now]:
            del _tickets[t]
        _tickets[ticket] = (email, now + TTL_SECONDS)
    return ticket


def redeem_ticket(ticket: str) -> str | None:
    """Consume a ticket and return the email it was issued to, else None.

    Popped whether or not it turns out to be expired, so a ticket can never be
    replayed even if the caller races the TTL.
    """
    if not ticket:
        return None
    with _lock:
        entry = _tickets.pop(ticket, None)
    if not entry:
        return None
    email, expires_at = entry
    return email if time.time() < expires_at else None
