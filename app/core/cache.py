"""Tiny in-process TTL cache for expensive read endpoints.

Same in-memory approach used for role/token caches in core.security — no
external dependency. Values live for `ttl` seconds; writers call `invalidate`
to drop a key so edits show immediately rather than waiting out the TTL.
"""

import threading
import time

_store: dict = {}  # key -> (value, expires_at)
_lock = threading.Lock()


def get(key: str):
    """Return the cached value for `key`, or None if absent/expired."""
    now = time.time()
    with _lock:
        entry = _store.get(key)
        if entry and now < entry[1]:
            return entry[0]
        if entry:
            del _store[key]
    return None


def set(key: str, value, ttl: float) -> None:
    with _lock:
        _store[key] = (value, time.time() + ttl)


def invalidate(key: str) -> None:
    with _lock:
        _store.pop(key, None)
