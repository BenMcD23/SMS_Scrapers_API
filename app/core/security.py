"""Token verification and role checks.

There are three access tiers, used as FastAPI dependencies:

  require_user          — any valid Google ID token (cadets included)
  require_staff_or_nco  — staff or NCO group members
  require_staff         — staff group members only

Roles come from Google Workspace group membership and are cached for
5 minutes so we don't hit the admin API on every request.
"""

import os
import threading
import time

from fastapi import Header, HTTPException
from google.oauth2 import id_token, service_account
from google.auth.transport import requests
from googleapiclient.discovery import build as google_build

from core.config import (
    GOOGLE_CLIENT_ID, GOOGLE_DOMAIN, STAFF_GROUP, NCO_GROUP,
    SA_EMAIL, SA_PRIVATE_KEY, IMPERSONATE_EMAIL, OWNER_EMAIL,
)

_role_cache: dict = {}
_role_cache_lock = threading.Lock()

# Verified-token cache. Google's verify_oauth2_token fetches signing certs over
# the network on every call, so without this we pay a Google round-trip on every
# request. Keyed by the raw token string → (idinfo, expires_at).
_token_cache: dict = {}
_token_cache_lock = threading.Lock()

# Reused across verifications so even cache misses share a single HTTP session
# (and its cert cache) rather than building a fresh one each time.
_google_request = requests.Request()


def verify_token(authorization: str) -> dict:
    # ponytail: dev-only fake token for local UI testing, pairs with the
    # frontend's AUTH_DEV_BYPASS. Inert unless DEV_FAKE_AUTH=1 (never in prod).
    if os.environ.get("DEV_FAKE_AUTH") == "1" and authorization == "Bearer dev-fake-token":
        return {"email": OWNER_EMAIL, "email_verified": True, "hd": GOOGLE_DOMAIN}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1]

    now = time.time()
    with _token_cache_lock:
        cached = _token_cache.get(token)
        if cached and now < cached[1]:
            return cached[0]

    try:
        idinfo = id_token.verify_oauth2_token(token, _google_request, GOOGLE_CLIENT_ID)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")
    if not idinfo.get("email_verified"):
        raise HTTPException(status_code=401, detail="Email not verified")

    # Only accept accounts in our Workspace. Prefer the hd claim (Workspace
    # tokens always carry it); fall back to the email suffix so the check still
    # holds if hd is ever absent. This is the sole gate keeping outside Google
    # accounts off every require_user/portal endpoint.
    email = idinfo.get("email", "")
    hd = idinfo.get("hd")
    domain_ok = hd == GOOGLE_DOMAIN if hd else email.lower().endswith(f"@{GOOGLE_DOMAIN}")
    if not domain_ok:
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


def _fetch_user_role(email: str) -> str | None:
    if not SA_EMAIL or not SA_PRIVATE_KEY:
        return None
    try:
        creds = _service_account_creds(
            ["https://www.googleapis.com/auth/admin.directory.group.member.readonly"]
        ).with_subject(IMPERSONATE_EMAIL)
        admin = google_build("admin", "directory_v1", credentials=creds, cache_discovery=False)
        for group, role in [(STAFF_GROUP, "staff"), (NCO_GROUP, "nco")]:
            try:
                admin.members().get(groupKey=group, memberKey=email).execute()
                return role
            except Exception:
                continue
        return None
    except Exception as e:
        print(f"[_fetch_user_role] error: {e}")
        return None


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


def get_roles_for_emails(emails: list[str]) -> dict[str, str | None]:
    """Role for each email in one pass — two group listings instead of one
    admin-API round-trip per user. Results are written into the same per-email
    cache used by get_user_role, so both paths stay consistent."""
    now = time.time()
    with _role_cache_lock:
        cached = {
            e: _role_cache[e][0]
            for e in emails
            if e in _role_cache and now < _role_cache[e][1]
        }
    missing = [e for e in emails if e not in cached]
    if not missing:
        return cached

    if not SA_EMAIL or not SA_PRIVATE_KEY:
        return {**cached, **{e: None for e in missing}}
    try:
        creds = _service_account_creds(
            ["https://www.googleapis.com/auth/admin.directory.group.member.readonly"]
        ).with_subject(IMPERSONATE_EMAIL)
        admin = google_build("admin", "directory_v1", credentials=creds, cache_discovery=False)
        staff = _fetch_group_members(admin, STAFF_GROUP)
        nco = _fetch_group_members(admin, NCO_GROUP)
    except Exception as e:
        print(f"[get_roles_for_emails] error: {e}")
        return {**cached, **{e: None for e in missing}}

    resolved = {}
    expiry = time.time() + 300
    with _role_cache_lock:
        for email in missing:
            key = email.lower()
            role = "staff" if key in staff else "nco" if key in nco else None
            resolved[email] = role
            _role_cache[email] = (role, expiry)
    return {**cached, **resolved}


def get_user_role(email: str) -> str | None:
    # ponytail: pairs with the dev-fake-token bypass above
    if os.environ.get("DEV_FAKE_AUTH") == "1" and email.lower() == OWNER_EMAIL.lower():
        return "staff"
    with _role_cache_lock:
        cached = _role_cache.get(email)
        if cached and time.time() < cached[1]:
            return cached[0]
    role = _fetch_user_role(email)
    with _role_cache_lock:
        _role_cache[email] = (role, time.time() + 300)
    return role


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def require_user(authorization: str = Header(None)) -> dict:
    return verify_token(authorization)


def require_staff(authorization: str = Header(None)) -> dict:
    idinfo = verify_token(authorization)
    if get_user_role(idinfo["email"]) != "staff":
        raise HTTPException(status_code=403, detail="Staff access required")
    return idinfo


def require_staff_or_nco(authorization: str = Header(None)) -> dict:
    idinfo = verify_token(authorization)
    if get_user_role(idinfo["email"]) not in ("staff", "nco"):
        raise HTTPException(status_code=403, detail="Staff or NCO access required")
    return idinfo


def require_owner(authorization: str = Header(None)) -> dict:
    """Developer-only access — restricted to the single OWNER_EMAIL account."""
    idinfo = verify_token(authorization)
    if idinfo.get("email", "").lower() != OWNER_EMAIL.lower():
        raise HTTPException(status_code=403, detail="Owner access required")
    return idinfo
