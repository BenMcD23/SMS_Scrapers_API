"""The Workspace-domain gate in verify_token.

Google's token verification is stubbed, so no network or real token is needed.
"""

import pytest
from fastapi import HTTPException

import core.security as security
from core.config import GOOGLE_DOMAIN

OUTSIDE = "someone.else.com"

BASE = {"email_verified": True, "exp": 9999999999, "sub": "x"}


@pytest.fixture(autouse=True)
def _no_real_google(monkeypatch):
    """Never let a test reach Google, and never let one test's verified token be
    served to another from the in-process cache."""
    monkeypatch.setattr(security, "_token_cache", {})
    monkeypatch.delenv("DEV_FAKE_AUTH", raising=False)


def verify(claims: dict) -> dict:
    """Run verify_token against controlled claims. The token string is unique per
    call so the cache never masks a case."""
    security.id_token.verify_oauth2_token = lambda *a, **k: claims
    return security.verify_token(f"Bearer tok-{id(claims)}")


def assert_rejected(claims: dict):
    with pytest.raises(HTTPException) as excinfo:
        verify(claims)
    assert excinfo.value.status_code in (401, 403)


def test_outside_domain_differs_from_ours():
    # Guards the rest of the module: every "rejected" case below is meaningless
    # if the outside domain were accidentally our own.
    assert OUTSIDE != GOOGLE_DOMAIN


@pytest.mark.parametrize("claims", [
    pytest.param({**BASE, "email": f"a@{GOOGLE_DOMAIN}", "hd": GOOGLE_DOMAIN}, id="hd-claim"),
    pytest.param({**BASE, "email": f"b@{GOOGLE_DOMAIN}"}, id="email-suffix-no-hd"),
])
def test_in_domain_accepted(claims):
    assert verify(claims)


@pytest.mark.parametrize("claims", [
    pytest.param({**BASE, "email": f"c@{GOOGLE_DOMAIN}", "hd": OUTSIDE}, id="outside-hd"),
    pytest.param({**BASE, "email": f"d@{OUTSIDE}"}, id="outside-email-no-hd"),
    # A domain that merely *ends* with ours must not slip through.
    pytest.param({**BASE, "email": f"e@evil-{GOOGLE_DOMAIN}"}, id="spoofed-suffix"),
    pytest.param(
        {"email_verified": False, "exp": 9999999999,
         "email": f"f@{GOOGLE_DOMAIN}", "hd": GOOGLE_DOMAIN},
        id="unverified-email",
    ),
])
def test_rejected(claims):
    assert_rejected(claims)
