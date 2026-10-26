"""GET /oc/me — the site asks this instead of keeping its own OC list."""

import pytest

from conftest import PERSONAS
from core import security


def test_oc_me_needs_a_token(api):
    assert api.get("/oc/me").status_code == 401


@pytest.mark.parametrize("persona,expected", [
    ("oc", True), ("staff", False), ("owner", False), ("snco", False), ("nco", False), ("cadet", False),
])
def test_oc_me_says_who_is_the_oc(api, persona, expected):
    r = api.get("/oc/me", headers=api.as_(persona))
    assert r.status_code == 200
    assert r.json() == {"is_oc": expected}


def test_oc_me_includes_every_address_on_the_list(api, monkeypatch):
    # Not just the first one, which is only special for committee emails.
    monkeypatch.setattr(security, "OC_EMAIL", f"someone@else, {PERSONAS['owner']['email']}")
    assert api.get("/oc/me", headers=api.as_("owner")).json() == {"is_oc": True}
