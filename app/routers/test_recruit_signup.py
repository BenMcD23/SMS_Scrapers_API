"""The recruitment-evening sign-up page: staff opening and closing it from SMS,
and parents adding their child from the QR code on the night (/recruit-signup).
The joining form it leads to is tested in test_recruits.py."""

from datetime import datetime, timedelta

import pytest

import routers.recruits as recruits
from core.recruit_form import london_now
from database.models import RecruitApplication, RecruitSignupSettings

STAFF = {"Authorization": "Bearer staff"}


def _dob(years_ago: int) -> str:
    today = london_now().date()
    return today.replace(year=today.year - years_ago, day=1).isoformat()


SIGNUP = {
    "cadetFirstName": "Alex",
    "cadetLastName": "Smith",
    "cadetDob": _dob(13),
    "parentName": "Jane Smith",
    "parentEmail": "jane@example.com",
    "parentMobile": "07700 900123",
}


@pytest.fixture
def opened(api):
    res = api.put("/recruits/sign-up", json={"open": True}, headers=STAFF)
    assert res.status_code == 200
    return res.json()


# ── who can open it ──────────────────────────────────────────────────────────

STAFF_ROUTES = [("get", "/recruits/sign-up"), ("put", "/recruits/sign-up")]


@pytest.mark.parametrize("method, path", STAFF_ROUTES)
def test_the_switch_needs_a_token(api, method, path):
    kwargs = {"json": {"open": True}} if method == "put" else {}
    assert getattr(api, method)(path, **kwargs).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method, path", STAFF_ROUTES)
def test_only_staff_can_see_or_flip_the_switch(api, db, method, path, persona):
    kwargs = {"json": {"open": True}} if method == "put" else {}
    res = getattr(api, method)(path, headers=api.as_(persona), **kwargs)
    assert res.status_code == 403
    assert api.get("/recruit-signup").json() == {"open": False}


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_member_can_open_it(api, persona):
    res = api.put("/recruits/sign-up", json={"open": True}, headers=api.as_(persona))
    assert res.status_code == 200 and res.json()["open"] is True


def test_the_switch_refuses_anything_but_open_true_or_false(api):
    assert api.put("/recruits/sign-up", json={}, headers=STAFF).status_code == 422
    assert api.put("/recruits/sign-up", json={"open": True, "until": "never"}, headers=STAFF).status_code == 422


# ── opening and closing ──────────────────────────────────────────────────────

def test_it_is_shut_until_staff_open_it(api):
    # Locked by default: a fresh database has no row at all.
    assert api.get("/recruit-signup").json() == {"open": False}
    view = api.get("/recruits/sign-up", headers=STAFF).json()
    assert view == {"open": False, "openUntil": None, "openedBy": None, "url": recruits.RECRUIT_FORM_URL}
    assert api.post("/recruit-signup", json=SIGNUP).status_code == 409


def test_opening_it_lasts_until_midnight_and_says_who(api, opened):
    tomorrow = london_now().date() + timedelta(days=1)
    assert opened["open"] is True
    assert opened["openUntil"] == datetime.combine(tomorrow, datetime.min.time()).isoformat()
    assert opened["openedBy"] == "staff@317atc.co.uk"
    # The QR code points at the joining form's address, with no token.
    assert opened["url"] == recruits.RECRUIT_FORM_URL
    assert api.get("/recruit-signup").json() == {"open": True}


def test_closing_it_stops_sign_ups_at_once(api, db, opened):
    res = api.put("/recruits/sign-up", json={"open": False}, headers=STAFF)
    assert res.json()["open"] is False and res.json()["openUntil"] is None
    assert api.get("/recruit-signup").json() == {"open": False}
    res = api.post("/recruit-signup", json=SIGNUP)
    assert res.status_code == 409 and res.json()["detail"] == recruits.SIGNUP_CLOSED
    assert db.query(RecruitApplication).count() == 0


def test_a_page_left_open_shuts_itself_the_next_day(api, db, opened):
    # Nobody has to remember to lock it after the evening.
    db.query(RecruitSignupSettings).one().open_until = london_now() - timedelta(minutes=1)
    db.commit()
    assert api.get("/recruit-signup").json() == {"open": False}
    assert api.get("/recruits/sign-up", headers=STAFF).json()["openUntil"] is None
    assert api.post("/recruit-signup", json=SIGNUP).status_code == 409


def test_opening_it_twice_keeps_one_settings_row(api, db, opened):
    api.put("/recruits/sign-up", json={"open": True}, headers=api.as_("owner"))
    assert db.query(RecruitSignupSettings).count() == 1


def test_the_public_status_says_nothing_about_who_opened_it(api, opened):
    assert api.get("/recruit-signup").json() == {"open": True}


# ── signing up ───────────────────────────────────────────────────────────────

def test_signing_up_adds_a_recruit_and_prefills_their_form(api, db, opened):
    res = api.post("/recruit-signup", json=SIGNUP)
    assert res.status_code == 201
    body = res.json()
    app = db.query(RecruitApplication).one()
    assert body == {"cadetFirstName": "Alex", "link": f"{recruits.RECRUIT_FORM_URL}/{app.token}",
                    "email": "jane@example.com"}
    assert (app.cadet_first_name, app.cadet_last_name, app.parent_name, app.parent_email) == (
        "Alex", "Smith", "Jane Smith", "jane@example.com")
    assert app.created_by == recruits.SIGNUP_CREATED_BY
    assert app.expires_at > london_now() + timedelta(days=recruits.LINK_DAYS - 1)
    # What they typed on the night is already in the joining form.
    assert app.data["cadet"]["fullName"] == "Alex Smith"
    assert app.data["cadet"]["dob"] == SIGNUP["cadetDob"]
    assert app.data["nok"]["mobile"] == "07700 900123"
    assert app.data["nok"]["email"] == "jane@example.com"
    assert app.data["signer"] == {"forename": "Jane", "surname": "Smith"}


def test_a_signed_up_recruit_shows_on_the_staff_list_like_any_other(api, opened):
    api.post("/recruit-signup", json=SIGNUP)
    [listed] = api.get("/recruits", headers=STAFF).json()
    assert listed["status"] == "not_started" and listed["createdBy"] == recruits.SIGNUP_CREATED_BY


def test_the_link_from_a_sign_up_opens_the_joining_form(api, opened):
    link = api.post("/recruit-signup", json=SIGNUP).json()["link"]
    view = api.get(f"/recruit-form/{link.rsplit('/', 1)[1]}").json()
    assert view["status"] == "open" and view["cadetFirstName"] == "Alex"


def test_signing_up_emails_the_parent_their_link(api, db, outbox, opened):
    api.post("/recruit-signup", json=SIGNUP)
    app = db.query(RecruitApplication).one()
    [mail] = outbox
    assert mail["to"] == "jane@example.com"
    assert mail["subject"] == "Alex's joining form for 317 Squadron"
    assert f"{recruits.RECRUIT_FORM_URL}/{app.token}" in mail["html"]
    assert mail["reply_to"] == recruits.RECRUIT_CONTACT_EMAIL
    assert f"{app.expires_at.day} {app.expires_at:%B %Y}" in mail["html"]


def test_non_ascii_names_go_through_and_are_escaped_in_the_email(api, db, outbox, opened):
    res = api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": "Zoë <b>", "parentName": "Siobhán Ní Bhriain"})
    assert res.status_code == 201
    assert db.query(RecruitApplication).one().cadet_first_name == "Zoë <b>"
    assert "Zoë &lt;b&gt;" in outbox[0]["html"] and "<b>" not in outbox[0]["html"]


def test_signing_up_twice_keeps_one_recruit_and_resends_without_showing_the_link(api, db, outbox, opened):
    first = api.post("/recruit-signup", json=SIGNUP).json()
    # A different case is still the same child and parent.
    again = api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": "ALEX", "parentEmail": "Jane@Example.com"})
    assert again.status_code == 201
    # Only a name and an email: not enough to be handed a form that may hold answers.
    assert again.json()["link"] is None
    assert db.query(RecruitApplication).count() == 1
    assert len(outbox) == 2 and first["link"] in outbox[1]["html"]


def test_signing_up_again_after_the_link_ran_out_gives_it_a_fresh_14_days(api, db, outbox, opened):
    api.post("/recruit-signup", json=SIGNUP)
    app = db.query(RecruitApplication).one()
    app.expires_at = london_now() - timedelta(days=1)
    db.commit()
    api.post("/recruit-signup", json=SIGNUP)
    db.refresh(app)
    assert app.expires_at > london_now() + timedelta(days=recruits.LINK_DAYS - 1)


def test_a_sibling_under_the_same_email_is_a_second_recruit(api, db, opened):
    api.post("/recruit-signup", json=SIGNUP)
    res = api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": "Sam"})
    assert res.json()["link"]
    assert db.query(RecruitApplication).count() == 2


def test_a_child_whose_form_is_already_submitted_gets_a_new_one(api, db, opened):
    # The old one is done with; it stays as it was for staff.
    api.post("/recruit-signup", json=SIGNUP)
    db.query(RecruitApplication).one().submitted_at = london_now()
    db.commit()
    assert api.post("/recruit-signup", json=SIGNUP).json()["link"]
    assert db.query(RecruitApplication).count() == 2


def test_names_with_wildcard_characters_only_match_themselves(api, db, opened):
    api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": "A%"})
    assert api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": "Ab"}).json()["link"]
    assert db.query(RecruitApplication).count() == 2


def test_a_bot_filling_the_hidden_field_is_thanked_and_ignored(api, db, outbox, opened):
    res = api.post("/recruit-signup", json={**SIGNUP, "website": "http://spam.example"})
    assert res.status_code == 201 and res.json()["link"] is None
    assert db.query(RecruitApplication).count() == 0 and outbox == []


# ── what it refuses ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", list(SIGNUP))
def test_every_field_is_needed(api, db, opened, field):
    body = {k: v for k, v in SIGNUP.items() if k != field}
    assert api.post("/recruit-signup", json=body).status_code == 422
    assert api.post("/recruit-signup", json={**body, field: "   "}).status_code == 422
    assert db.query(RecruitApplication).count() == 0


@pytest.mark.parametrize("email", ["jane", "jane@example", "jane @example.com"])
def test_a_bad_email_is_refused_in_words(api, opened, email):
    res = api.post("/recruit-signup", json={**SIGNUP, "parentEmail": email})
    assert res.status_code == 422 and "email address" in res.json()["detail"]


@pytest.mark.parametrize("mobile", ["0770", "call me", "07700-90"])
def test_a_bad_mobile_is_refused_in_words(api, opened, mobile):
    res = api.post("/recruit-signup", json={**SIGNUP, "parentMobile": mobile})
    assert res.status_code == 422 and "mobile number" in res.json()["detail"]


@pytest.mark.parametrize("dob", [_dob(0), _dob(5), _dob(19), _dob(40), "2999-01-01"])
def test_a_mistyped_date_of_birth_is_refused(api, opened, dob):
    res = api.post("/recruit-signup", json={**SIGNUP, "cadetDob": dob})
    assert res.status_code == 422 and res.json()["detail"] == "Check your child's date of birth."


@pytest.mark.parametrize("years", [9, 12, 18])
def test_children_from_nine_to_eighteen_can_sign_up(api, opened, years):
    # Younger ones come along to recruitment evenings before they can join.
    assert api.post("/recruit-signup", json={**SIGNUP, "cadetDob": _dob(years)}).status_code == 201


def test_a_date_of_birth_that_isnt_a_date_is_refused(api, opened):
    assert api.post("/recruit-signup", json={**SIGNUP, "cadetDob": "13/02/2013"}).status_code == 422


def test_extra_fields_are_refused(api, db, opened):
    assert api.post("/recruit-signup", json={**SIGNUP, "token": "x"}).status_code == 422
    assert db.query(RecruitApplication).count() == 0


def test_an_overlong_name_is_refused(api, opened):
    assert api.post("/recruit-signup", json={**SIGNUP, "cadetLastName": "x" * 201}).status_code == 422


def test_sign_ups_are_rate_limited(api, opened):
    # The page is public: a script can't fill the recruits list.
    codes = [api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": f"Kid{i}"}).status_code
             for i in range(31)]
    assert codes[:30] == [201] * 30 and codes[30] == 429


def test_a_made_up_bearer_token_does_not_buy_a_fresh_rate_limit(api, opened):
    # The limiter keys callers by their bearer token without verifying it, so on
    # this public route a script sending a new junk token each time would get a
    # new bucket per request, and an unlimited stream of emails to any address.
    codes = [api.post("/recruit-signup", json={**SIGNUP, "cadetFirstName": f"Kid{i}"},
                      headers={"Authorization": f"Bearer junk{i}"}).status_code
             for i in range(31)]
    assert codes[30] == 429
