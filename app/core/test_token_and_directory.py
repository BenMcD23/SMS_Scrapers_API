"""verify_token's caching and failure modes, the role dependencies, and the
Workspace directory lookup the scraper matches cadet emails against."""

import time

import pytest
from fastapi import HTTPException
from google.auth import exceptions as google_auth_exceptions

import core.directory as directory
import core.security as security

GOOD = {"email": "a@317atc.co.uk", "email_verified": True, "hd": "317atc.co.uk", "sub": "1"}


@pytest.fixture
def google(monkeypatch):
    calls = []
    answer = {"value": dict(GOOD, exp=time.time() + 600)}

    def verify(token, request, client_id, clock_skew_in_seconds):
        calls.append(token)
        value = answer["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(security.id_token, "verify_oauth2_token", verify)
    monkeypatch.setattr(security, "_token_cache", {})
    monkeypatch.delenv("DEV_FAKE_AUTH", raising=False)
    return calls, answer


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer"])
def test_missing_or_malformed_header_is_401(google, header):
    with pytest.raises(HTTPException) as e:
        security.verify_token(header)
    assert e.value.status_code == 401
    assert google[0] == []


def test_a_verified_token_is_cached_until_it_expires(google):
    calls, answer = google
    assert security.verify_token("Bearer t1")["email"] == "a@317atc.co.uk"
    security.verify_token("Bearer t1")
    assert calls == ["t1"]

    answer["value"] = dict(GOOD, exp=time.time() - 1)
    security.verify_token("Bearer t2")
    security.verify_token("Bearer t2")
    # Already expired: never served from cache.
    assert calls == ["t1", "t2", "t2"]


def test_cache_is_capped_at_an_hour(google):
    calls, answer = google
    answer["value"] = dict(GOOD, exp=time.time() + 86400)
    security.verify_token("Bearer long")
    _, expires_at = security._token_cache["long"]
    assert expires_at <= time.time() + 3600 + 1


def test_google_unreachable_is_503_not_401(google):
    _, answer = google
    answer["value"] = google_auth_exceptions.TransportError("cert fetch failed")
    with pytest.raises(HTTPException) as e:
        security.verify_token("Bearer t")
    assert e.value.status_code == 503


@pytest.mark.parametrize("value,code", [
    (ValueError("Token expired"), 401),
    (dict(GOOD, email_verified=False), 401),
    (dict(GOOD, hd="gmail.com"), 403),
    ({k: v for k, v in GOOD.items() if k != "hd"} | {"email": "x@gmail.com"}, 403),
])
def test_rejections(google, value, code):
    google[1]["value"] = value
    with pytest.raises(HTTPException) as e:
        security.verify_token("Bearer t")
    assert e.value.status_code == code


def test_role_dependencies(monkeypatch):
    monkeypatch.setattr(security, "verify_token", lambda auth: {"email": auth})
    roles = {"s@x": "staff", "sn@x": "snco", "n@x": "nco"}
    monkeypatch.setattr(security, "get_user_role", lambda email: roles.get(email))
    monkeypatch.setattr(security, "OWNER_EMAIL", "Owner@X")
    monkeypatch.setattr(security, "OC_EMAIL", "oc@x")

    table = {
        security.require_staff: {"s@x"},
        security.require_staff_or_snco: {"s@x", "sn@x"},
        security.require_staff_or_nco: {"s@x", "sn@x", "n@x"},
        security.require_owner: {"owner@x"},
        security.require_oc: {"OC@X"},
    }
    for dep, allowed in table.items():
        for who in ("s@x", "sn@x", "n@x", "c@x", "owner@x", "OC@X"):
            if who in allowed:
                assert dep(who)["email"] == who
            else:
                with pytest.raises(HTTPException) as e:
                    dep(who)
                assert e.value.status_code == 403, (dep.__name__, who)


def test_is_oc_needs_a_configured_address(monkeypatch):
    monkeypatch.setattr(security, "OC_EMAIL", "")
    assert security.is_oc("anyone@x") is False
    assert security.is_oc("") is False


def test_roles_for_many_emails_refreshes_once_for_unknowns(monkeypatch):
    calls = []

    def groups(unknown=False):
        calls.append(unknown)
        return {"staff": {"a@x"}} if not unknown else {"staff": {"a@x"}, "nco": {"new@x"}}

    monkeypatch.setattr(security, "_groups", groups)
    assert security.get_roles_for_emails(["A@x", "new@x"]) == {"A@x": "staff", "new@x": "nco"}
    assert calls == [False, True]


# ── Workspace directory ───────────────────────────────────────────────────────

class FakeDirectory:
    def __init__(self, pages):
        self.pages = list(pages)

    def users(self):
        return self

    def list(self, **kw):
        return self

    def execute(self):
        return self.pages.pop(0)


def test_workspace_users_pages_and_skips_nameless_accounts(monkeypatch):
    class Creds:
        def with_subject(self, s):
            return self

    monkeypatch.setattr(directory.service_account.Credentials, "from_service_account_info",
                        lambda info, scopes: Creds())
    monkeypatch.setattr(directory, "build", lambda *a, **k: FakeDirectory([
        {"users": [{"primaryEmail": "amy@x", "name": {"givenName": " Amy ", "familyName": "Able"}},
                   {"primaryEmail": "room@x"},
                   {"name": {"givenName": "No"}}],
         "nextPageToken": "p2"},
        {"users": [{"primaryEmail": "bob@x", "name": {"givenName": None, "familyName": "Baker"}}]},
    ]))
    users = directory.get_workspace_users()
    assert [(u["email"], u["first_name_key"], u["last_name_key"]) for u in users] == [
        ("amy@x", "AMY", "ABLE"), ("room@x", "", ""), ("bob@x", "", "BAKER")]
