"""The shared group snapshot behind get_user_role, and the signing-cert cache.
Google is stubbed: _directory/_fetch_group_members and the HTTP layer."""

import pytest

import core.security as security

GROUPS = {"staff@x": "staff", "snco@x": "snco", "nco@x": "nco"}


@pytest.fixture
def google(monkeypatch):
    """Counts group fetches; set .fail to make Google error."""
    state = type("G", (), {"fetches": 0, "fail": False, "members": dict(GROUPS)})()
    group_role = {security.STAFF_GROUP: "staff", security.SNCO_GROUP: "snco", security.NCO_GROUP: "nco"}

    def fetch(admin, group):
        if state.fail:
            raise RuntimeError("google down")
        state.fetches += 1
        return {e for e, r in state.members.items() if r == group_role[group]}

    clock = [1000.0]
    monkeypatch.setattr(security.time, "time", lambda: clock[0])
    monkeypatch.setattr(security, "_group_snapshot", None)
    monkeypatch.setattr(security, "SA_EMAIL", "sa@x")
    monkeypatch.setattr(security, "SA_PRIVATE_KEY", "key")
    monkeypatch.setattr(security, "_directory", lambda: None)
    monkeypatch.setattr(security, "_fetch_group_members", fetch)
    monkeypatch.delenv("DEV_FAKE_AUTH", raising=False)
    state.clock = clock
    return state


def test_one_snapshot_serves_everyone(google):
    assert [security.get_user_role(e) for e in GROUPS] == ["staff", "snco", "nco"]
    assert security.get_roles_for_emails(list(GROUPS)) == GROUPS
    assert google.fetches == 3  # three groups, once


def test_refreshes_after_ttl(google):
    security.get_user_role("staff@x")
    google.clock[0] += security.ROLE_CACHE_S + 1
    security.get_user_role("staff@x")
    assert google.fetches == 6


def test_new_member_gets_in_within_a_minute_but_no_sooner(google):
    assert security.get_user_role("new@x") is None
    google.members["new@x"] = "staff"
    google.clock[0] += 10
    assert security.get_user_role("new@x") is None  # unknown refresh is rate-limited
    google.clock[0] += security.ROLE_MISS_REFRESH_S
    assert security.get_user_role("new@x") == "staff"


def test_google_failure_keeps_last_snapshot(google):
    security.get_user_role("staff@x")
    google.fail = True
    google.clock[0] += security.ROLE_CACHE_S + 1
    assert security.get_user_role("staff@x") == "staff"


def test_certs_cached_for_max_age(monkeypatch):
    calls = []

    class Resp:
        status = 200
        headers = {"cache-control": "public, max-age=100"}

    monkeypatch.setattr(security.requests.Request, "__call__", lambda self, url, method="GET", **k: calls.append(url) or Resp())
    clock = [0.0]
    monkeypatch.setattr(security.time, "time", lambda: clock[0])
    req = security._CertCachingRequest()
    url = security.id_token._GOOGLE_OAUTH2_CERTS_URL
    req(url)
    req(url)
    assert len(calls) == 1
    clock[0] = 101
    req(url)
    req("https://example.com/other")
    req("https://example.com/other")
    assert len(calls) == 4  # expired → refetched; other URLs never cached
