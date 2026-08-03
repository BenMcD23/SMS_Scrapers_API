"""Request rate limiting.

The API is published to the public internet over Tailscale Funnel, so a valid
Workspace token is the only thing standing between a caller and endpoints that
cost real money (GOV.UK Notify sends) or enumerate the cadet roster.

Buckets are per-caller, not per-IP: every request arrives through the Funnel
proxy, so the usual remote-address key would put the whole squadron in one
bucket and one busy user would lock everyone else out.
"""

import hashlib

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _caller_key(request: Request) -> str:
    """Identify the caller by their bearer token rather than their address.

    Hashed so raw ID tokens don't end up in the limiter's in-memory keys. A
    token refresh (roughly hourly) starts a fresh bucket, which is why the
    limits below use windows well under an hour.

    Falls back to the remote address for unauthenticated routes such as /ping.
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return hashlib.sha256(auth[7:].encode()).hexdigest()[:32]
    return get_remote_address(request)


# The default is a generous backstop against a runaway client; the endpoints
# that actually warrant tight limits carry their own @limiter.limit decorator.
limiter = Limiter(key_func=_caller_key, default_limits=["600/minute"])
