"""Token verification and role checks.

There are three access tiers, used as FastAPI dependencies:

  require_user          — any valid Google ID token (cadets included)
  require_staff_or_nco  — staff, SNCO or NCO group members
  require_staff_or_snco — staff or SNCO group members (inspections)
  require_staff         — staff group members only

Roles come from Google Workspace group membership, from a snapshot of the
groups refreshed every 15 minutes so we don't hit the admin API per request.
"""

import logging
import os
import re
import threading
import time

import httplib2
from fastapi import Header, HTTPException
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests
from google.oauth2 import id_token, service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build as google_build

from core.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_DOMAIN,
    IMPERSONATE_EMAIL,
    NCO_GROUP,
    OC_EMAIL,
    OWNER_EMAIL,
    SA_EMAIL,
    SA_PRIVATE_KEY,
    SNCO_GROUP,
    STAFF_GROUP,
)

logger = logging.getLogger(__name__)

# Roles come from one snapshot of the three groups, shared by every request:
# (fetched_at, {role: member emails}). See _groups().
ROLE_CACHE_S = 900
ROLE_MISS_REFRESH_S = 60
_group_snapshot: tuple[float, dict[str, set[str]]] | None = None
_group_lock = threading.Lock()

# Directory API credentials, shared so the access token is reused for its hour
# instead of exchanged on every lookup; they refresh themselves when it expires.
_directory_creds = None

# Verified-token cache. Keyed by the raw token string → (idinfo, expires_at).
_token_cache: dict = {}
_token_cache_lock = threading.Lock()


class _CertCachingRequest(requests.Request):
    """google-auth fetches Google's signing certs on every token verification.
    Keep them for as long as Google's Cache-Control allows (hours), so a new
    token costs no round-trip. Google publishes new keys well before using them."""

    def __init__(self):
        super().__init__()
        self._certs = None  # (response, expires_at)

    def __call__(self, url, method="GET", **kwargs):
        if method != "GET" or url != id_token._GOOGLE_OAUTH2_CERTS_URL:
            return super().__call__(url, method=method, **kwargs)
        cached = self._certs
        if cached and time.time() < cached[1]:
            return cached[0]
        resp = super().__call__(url, method=method, **kwargs)
        max_age = re.search(r"max-age=(\d+)", resp.headers.get("cache-control", ""))
        if resp.status == 200 and max_age:
            self._certs = (resp, time.time() + int(max_age[1]))
        return resp


_google_request = _CertCachingRequest()

# google-auth defaults this to 0, meaning a token whose `iat` is even one second
# ahead of this host's clock is rejected as "Token used too early". That lands
# on the user as a 401 on the very token they just signed in with — the app
# shows "Session expired", and a refresh a few seconds later works fine. Any
# drift between here and Google is enough to trigger it.
CLOCK_SKEW_S = 60


def _dev_fake_email(role: str) -> str:
    """Identity the dev bypass logs in as for a role. Staff keeps the owner
    account so owner-only pages stay reachable; SNCO/NCO get their own accounts
    so role checks and the rows they create match what a real one would see."""
    return OWNER_EMAIL if role == "staff" else f"dev.{role}@{GOOGLE_DOMAIN}"


def verify_token(authorization: str) -> dict:
    # ponytail: dev-only fake token for local UI testing, pairs with the
    # frontend's AUTH_DEV_BYPASS. Inert unless DEV_FAKE_AUTH=1 (never in prod).
    # "Bearer dev-fake-token" is staff; ":snco"/":nco" suffix picks that role.
    if os.environ.get("DEV_FAKE_AUTH") == "1" and (authorization or "").startswith("Bearer dev-fake-token"):
        role = authorization.split("dev-fake-token", 1)[1].lstrip(":") or "staff"
        if role not in ("staff", "snco", "nco"):
            raise HTTPException(status_code=401, detail="Invalid Token")
        return {
            "email": _dev_fake_email(role), "email_verified": True, "hd": GOOGLE_DOMAIN,
            # A stable sub per role so get_or_create_user works under the bypass.
            "sub": "dev-fake-sub" if role == "staff" else f"dev-fake-sub-{role}",
            "given_name": "Dev", "family_name": "Owner" if role == "staff" else role.upper(),
        }

    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("auth rejected: no bearer token on request")
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1]

    now = time.time()
    with _token_cache_lock:
        cached = _token_cache.get(token)
        if cached and now < cached[1]:
            return cached[0]

    try:
        idinfo = id_token.verify_oauth2_token(
            token, _google_request, GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=CLOCK_SKEW_S,
        )
    except google_auth_exceptions.TransportError as e:
        # We couldn't reach Google to fetch its signing certs — that says
        # nothing about the token. Answering 401 here would tell the frontend
        # the session is stale and send the user round the sign-in loop again,
        # so surface it as what it is: this service is temporarily degraded.
        logger.error(f"token verification: Google cert fetch failed: {e}")
        raise HTTPException(status_code=503, detail="Auth check unavailable")
    except Exception as e:
        # Log the real reason — "Invalid Token" alone makes an expired token, a
        # client-ID mismatch and clock skew look identical from the outside.
        logger.warning(f"token rejected: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail="Invalid Token")
    if not idinfo.get("email_verified"):
        logger.warning(f"auth rejected: {idinfo.get('email')} email not verified")
        raise HTTPException(status_code=401, detail="Email not verified")

    # Only accept accounts in our Workspace. Prefer the hd claim (Workspace
    # tokens always carry it); fall back to the email suffix so the check still
    # holds if hd is ever absent. This is the sole gate keeping outside Google
    # accounts off every require_user/portal endpoint.
    email = idinfo.get("email", "")
    hd = idinfo.get("hd")
    domain_ok = hd == GOOGLE_DOMAIN if hd else email.lower().endswith(f"@{GOOGLE_DOMAIN}")
    if not domain_ok:
        logger.warning(f"auth rejected: {email} (hd={hd}) is outside {GOOGLE_DOMAIN}")
        raise HTTPException(status_code=403, detail="Outside this Workspace")

    # Cache until the token's own expiry (capped at 1h), so a revoked/expired
    # token is never served from cache past its lifetime.
    expires_at = min(idinfo.get("exp", now), now + 3600)
    with _token_cache_lock:
        # Prune expired entries to keep the cache bounded.
        for tok in [t for t, (_, exp) in _token_cache.items() if exp <= now]:
            del _token_cache[tok]
        _token_cache[token] = (idinfo, expires_at)
    return idinfo


def _service_account_creds(scopes: list[str]):
    return service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": SA_EMAIL,
            "private_key": SA_PRIVATE_KEY,
            "token_uri": "https://oauth2.googleapis.com/token",
            "private_key_id": "",
            "client_id": "",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        },
        scopes=scopes,
    )


def _directory():
    """A Directory API client on the shared credentials. Built per call because
    httplib2 isn't thread-safe; the build itself is local (static discovery)."""
    global _directory_creds
    if _directory_creds is None:
        _directory_creds = _service_account_creds(
            ["https://www.googleapis.com/auth/admin.directory.group.member.readonly"]
        ).with_subject(IMPERSONATE_EMAIL)
    http = AuthorizedHttp(_directory_creds, http=httplib2.Http(timeout=10))
    return google_build("admin", "directory_v1", http=http, cache_discovery=False)


def _fetch_group_members(admin, group: str) -> set[str]:
    """All member emails of a Workspace group, lower-cased, following pagination."""
    members: set[str] = set()
    page_token = None
    while True:
        resp = admin.members().list(
            groupKey=group, maxResults=200, pageToken=page_token
        ).execute()
        members.update(
            m["email"].lower() for m in resp.get("members", []) if m.get("email")
        )
        page_token = resp.get("nextPageToken")
        if not page_token:
            return members


def _groups(unknown: bool = False) -> dict[str, set[str]] | None:
    """{role: member emails}, from a snapshot of all three groups shared by every
    request. Refreshed after ROLE_CACHE_S, or after ROLE_MISS_REFRESH_S when
    someone isn't in it (so a newly added member gets in within a minute).
    One fetch at a time: concurrent cold requests wait for it and share it.
    If Google fails, the last snapshot is kept rather than locking everyone out."""
    global _group_snapshot
    with _group_lock:
        age = time.time() - _group_snapshot[0] if _group_snapshot else None
        if age is not None and age < (ROLE_MISS_REFRESH_S if unknown else ROLE_CACHE_S):
            return _group_snapshot[1]
        if not SA_EMAIL or not SA_PRIVATE_KEY:
            return None
        try:
            admin = _directory()
            groups = {role: _fetch_group_members(admin, group)
                      for role, group in (("staff", STAFF_GROUP), ("snco", SNCO_GROUP), ("nco", NCO_GROUP))}
        except Exception as e:
            logger.error(f"group lookup failed, keeping the previous snapshot: {e}")
            return _group_snapshot[1] if _group_snapshot else None
        _group_snapshot = (time.time(), groups)
        return groups


def _role_in(groups: dict[str, set[str]] | None, email: str) -> str | None:
    key = (email or "").lower()
    return next((role for role, members in (groups or {}).items() if key in members), None)


def get_roles_for_emails(emails: list[str]) -> dict[str, str | None]:
    """Role for each email, from the shared group snapshot."""
    groups = _groups()
    if any(_role_in(groups, e) is None for e in emails):
        groups = _groups(unknown=True)
    return {e: _role_in(groups, e) for e in emails}


def get_user_role(email: str) -> str | None:
    # ponytail: pairs with the dev-fake-token bypass above — the role is decided
    # by which fake account the token logged in as, no group lookup.
    if os.environ.get("DEV_FAKE_AUTH") == "1":
        for role in ("staff", "snco", "nco"):
            if (email or "").lower() == _dev_fake_email(role).lower():
                return role
    return _role_in(_groups(), email) or _role_in(_groups(unknown=True), email)


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def require_user(authorization: str = Header(None)) -> dict:
    return verify_token(authorization)


def require_staff(authorization: str = Header(None)) -> dict:
    idinfo = verify_token(authorization)
    role = get_user_role(idinfo["email"])
    if role != "staff":
        logger.warning(f"staff check failed: {idinfo['email']} has role {role!r}")
        raise HTTPException(status_code=403, detail="Staff access required")
    return idinfo


def require_staff_or_snco(authorization: str = Header(None)) -> dict:
    """Inspections — SNCOs run them, so they get the same access as staff there."""
    idinfo = verify_token(authorization)
    if get_user_role(idinfo["email"]) not in ("staff", "snco"):
        raise HTTPException(status_code=403, detail="Staff or SNCO access required")
    return idinfo


def require_staff_or_nco(authorization: str = Header(None)) -> dict:
    idinfo = verify_token(authorization)
    if get_user_role(idinfo["email"]) not in ("staff", "snco", "nco"):
        raise HTTPException(status_code=403, detail="Staff or NCO access required")
    return idinfo


def require_owner(authorization: str = Header(None)) -> dict:
    """Developer-only access — restricted to the single OWNER_EMAIL account."""
    idinfo = verify_token(authorization)
    if idinfo.get("email", "").lower() != OWNER_EMAIL.lower():
        raise HTTPException(status_code=403, detail="Owner access required")
    return idinfo


def is_oc(email: str) -> bool:
    """True only if this email matches the configured OC_EMAIL (case-insensitive).
    False when OC_EMAIL is unset — no owner backdoor, so only the OC can approve.
    To drive the committee flow with DEV_FAKE_AUTH locally, set OC_EMAIL to the
    owner email."""
    e = (email or "").lower()
    return bool(e) and bool(OC_EMAIL) and e == OC_EMAIL.lower()


def require_oc(authorization: str = Header(None)) -> dict:
    """OC-only access — committee-request approvals and the OC dashboard."""
    idinfo = verify_token(authorization)
    if not is_oc(idinfo.get("email", "")):
        raise HTTPException(status_code=403, detail="OC access required")
    return idinfo
