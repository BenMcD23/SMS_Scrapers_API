"""Pinned pages: kept on the account so they follow someone between browsers.

Every role that uses the SMS site can read and save its own list; cadets (the
portal only) and anyone signed out can't. What's stored is a list of site
paths, once each, in order; anything else is refused rather than stored.
"""

import pytest

from database.models import User

URL = "/settings/nav-pins"


def test_signed_out_is_turned_away(api):
    assert api.get(URL).status_code == 401
    assert api.put(URL, json={"pins": ["/stats"]}).status_code == 401


def test_a_cadet_has_no_pins_to_keep(api):
    assert api.get(URL, headers=api.as_("cadet")).status_code == 403
    assert api.put(URL, json={"pins": ["/stats"]}, headers=api.as_("cadet")).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc", "snco", "nco"])
def test_every_site_role_starts_with_none_chosen_and_keeps_what_it_saves(api, persona):
    headers = api.as_(persona)
    # Never chosen: null, so the site shows the role's defaults.
    assert api.get(URL, headers=headers).json() == {"pins": None}

    res = api.put(URL, json={"pins": ["/stores/badges/orders", "/session-plans"]}, headers=headers)
    assert res.status_code == 200
    assert res.json() == {"pins": ["/stores/badges/orders", "/session-plans"]}
    # And it's there next time, from any browser.
    assert api.get(URL, headers=headers).json() == {"pins": ["/stores/badges/orders", "/session-plans"]}


def test_each_person_has_their_own(api):
    api.put(URL, json={"pins": ["/stats"]}, headers=api.as_("staff"))
    api.put(URL, json={"pins": ["/check-in"]}, headers=api.as_("nco"))
    assert api.get(URL, headers=api.as_("staff")).json() == {"pins": ["/stats"]}
    assert api.get(URL, headers=api.as_("nco")).json() == {"pins": ["/check-in"]}
    assert api.get(URL, headers=api.as_("staff2")).json() == {"pins": None}


def test_as_many_as_they_like_in_their_order_once_each(api):
    pins = [f"/page-{i}" for i in range(30)]
    res = api.put(URL, json={"pins": [*pins, "/page-3"]}, headers=api.as_("staff"))
    assert res.json() == {"pins": pins}


def test_an_empty_list_is_kept_empty_and_null_puts_the_defaults_back(api, db):
    headers = api.as_("staff")
    api.put(URL, json={"pins": []}, headers=headers)
    assert api.get(URL, headers=headers).json() == {"pins": []}
    api.put(URL, json={"pins": None}, headers=headers)
    assert api.get(URL, headers=headers).json() == {"pins": None}
    assert db.query(User).filter(User.email.like("staff@%")).one().nav_pins is None


@pytest.mark.parametrize(
    "pins",
    [
        ["stores"],  # not a path
        ["https://evil.example/x"],  # a full URL
        ["//evil.example/x"],  # protocol-relative
        ["/has space"],
        ["/" + "a" * 200],  # too long
        [f"/p{i}" for i in range(201)],  # too many
        "/stats",  # not a list
        [1, 2],  # not strings
    ],
)
def test_anything_but_a_list_of_site_paths_is_refused(api, pins):
    headers = api.as_("staff")
    assert api.put(URL, json={"pins": pins}, headers=headers).status_code == 422
    # And nothing was stored.
    assert api.get(URL, headers=headers).json() == {"pins": None}


def test_a_missing_body_is_refused(api):
    assert api.put(URL, headers=api.as_("staff")).status_code == 422
