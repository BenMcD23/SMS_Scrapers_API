"""Dev fake-auth: the token suffix decides the role, and it stays inert
without DEV_FAKE_AUTH=1."""

import pytest
from fastapi import HTTPException

from core.security import verify_token, get_user_role


def test_dev_token_roles(monkeypatch):
    monkeypatch.setenv("DEV_FAKE_AUTH", "1")
    for token, role in [
        ("Bearer dev-fake-token", "staff"),
        ("Bearer dev-fake-token:snco", "snco"),
        ("Bearer dev-fake-token:nco", "nco"),
    ]:
        idinfo = verify_token(token)
        assert get_user_role(idinfo["email"]) == role
    # Distinct identities, so NCO/SNCO rows aren't mixed with the owner's.
    subs = {verify_token(f"Bearer dev-fake-token:{r}")["sub"] for r in ("staff", "snco", "nco")}
    assert len(subs) == 3

    with pytest.raises(HTTPException):
        verify_token("Bearer dev-fake-token:cadet")


def test_dev_token_inert_without_flag(monkeypatch):
    monkeypatch.delenv("DEV_FAKE_AUTH", raising=False)
    with pytest.raises(HTTPException):
        verify_token("Bearer dev-fake-token:nco")
