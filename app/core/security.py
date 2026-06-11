"""Token verification and role checks.

There are three access tiers, used as FastAPI dependencies:

  require_user          — any valid Google ID token (cadets included)
  require_staff_or_nco  — staff or NCO group members
  require_staff         — staff group members only

Roles come from Google Workspace group membership and are cached for
5 minutes so we don't hit the admin API on every request.
"""

import threading
import time

from fastapi import Header, HTTPException
from google.oauth2 import id_token, service_account
from google.auth.transport import requests
from googleapiclient.discovery import build as google_build

from core.config import (
    GOOGLE_CLIENT_ID, STAFF_GROUP, NCO_GROUP,
    SA_EMAIL, SA_PRIVATE_KEY, IMPERSONATE_EMAIL,
)

_role_cache: dict = {}
_role_cache_lock = threading.Lock()


def verify_token(authorization: str) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        token = authorization.split(" ", 1)[1]
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")
    if not idinfo.get("email_verified"):
        raise HTTPException(status_code=401, detail="Email not verified")
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


def get_user_role(email: str) -> str | None:
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
