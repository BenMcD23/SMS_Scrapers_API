"""User settings: Bader credentials, signature, profile (incl. encrypted bank
details) and assessor name. The phone-number endpoints have their own file."""

import pytest

from core.crypto import decrypt_password
from database.models import BaderCredentials, User, UserProfile

STAFF = {"Authorization": "Bearer staff"}
NCO = {"Authorization": "Bearer nco"}

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _user(db, email):
    return db.query(User).filter(User.email == email).one()


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/get-signature"), ("delete", "/delete-signature"), ("get", "/settings/user-profile"),
    ("patch", "/settings/user-profile"), ("get", "/settings/assessor-name"), ("patch", "/settings/assessor-name"),
    ("get", "/settings/phone-number"), ("patch", "/settings/phone-number"),
])
def test_nco_and_above_only(api, method, path):
    def call(h):
        if method in ("get", "delete"):
            return getattr(api, method)(path, headers=h)
        return getattr(api, method)(path, json={}, headers=h)

    assert call({}).status_code == 401
    assert call(api.as_("cadet")).status_code == 403


def test_bader_credentials_are_staff_only(api):
    assert api.post("/save-credentials", json={}, headers=NCO).status_code == 403
    assert api.post("/save-credentials", json={}, headers=api.as_("snco")).status_code == 403


# ── Bader credentials ─────────────────────────────────────────────────────────

def test_credentials_are_encrypted_at_rest(api, db):
    res = api.post("/save-credentials", headers=STAFF, json={
        "role_user": " role ", "role_pass": " s3cret ", "pers_user": "me", "pers_pass": "pw"})
    assert res.status_code == 200 and "staff@317atc.co.uk" in res.json()["message"]
    creds = db.query(BaderCredentials).one()
    assert creds.role_username == "role" and creds.personal_username == "me"
    assert creds.role_password != " s3cret "
    assert decrypt_password(creds.role_password) == " s3cret "
    assert decrypt_password(creds.personal_password) == "pw"


def test_saving_one_login_keeps_the_other(api, db):
    api.post("/save-credentials", headers=STAFF, json={
        "role_user": "role", "role_pass": "rp", "pers_user": "me", "pers_pass": "pp"})
    # The form comes back blank; only the role password is retyped.
    api.post("/save-credentials", headers=STAFF, json={
        "role_user": "", "role_pass": "new", "pers_user": "", "pers_pass": ""})
    creds = db.query(BaderCredentials).one()
    db.refresh(creds)
    assert creds.role_username == "role" and creds.personal_username == "me"
    assert decrypt_password(creds.role_password) == "new"
    assert decrypt_password(creds.personal_password) == "pp"
    assert db.query(BaderCredentials).count() == 1


def test_empty_first_save_creates_an_empty_row(api, db):
    assert api.post("/save-credentials", json={}, headers=STAFF).status_code == 200
    creds = db.query(BaderCredentials).one()
    assert creds.role_username is None and creds.role_password is None


# ── signature ─────────────────────────────────────────────────────────────────

def test_signature_round_trip_and_replace(api, db):
    assert api.get("/get-signature", headers=NCO).status_code == 404
    res = api.post("/save-signature", files={"file": ("s.png", PNG, "image/png")}, headers=NCO)
    assert res.status_code == 200
    got = api.get("/get-signature", headers=NCO)
    assert got.content == PNG and got.headers["content-type"] == "image/png"

    api.post("/save-signature", files={"file": ("s.jpg", b"\xff\xd8jpeg", "image/jpeg")}, headers=NCO)
    assert api.get("/get-signature", headers=NCO).headers["content-type"] == "image/jpeg"

    # Each user sees only their own.
    assert api.get("/get-signature", headers=STAFF).status_code == 404

    assert api.delete("/delete-signature", headers=NCO).status_code == 200
    assert api.delete("/delete-signature", headers=NCO).status_code == 404
    assert api.get("/get-signature", headers=NCO).status_code == 404


@pytest.mark.parametrize("name,data,mime,detail", [
    ("s.gif", b"GIF89a", "image/gif", "PNG or JPEG"),
    ("s.svg", b"<svg/>", "image/svg+xml", "PNG or JPEG"),
    ("s.png", b"0" * (2 * 1024 * 1024 + 1), "image/png", "2 MB"),
])
def test_signature_rejections(api, name, data, mime, detail):
    res = api.post("/save-signature", files={"file": (name, data, mime)}, headers=NCO)
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_signature_needs_a_file(api):
    assert api.post("/save-signature", headers=NCO).status_code == 422


# ── profile ───────────────────────────────────────────────────────────────────

def test_profile_defaults_before_anything_is_saved(api):
    body = api.get("/settings/user-profile", headers=NCO).json()
    assert body["first_name"] == "Nina" and body["last_name"] == "Corporal"
    assert all(body[k] == "" for k in body if k not in ("first_name", "last_name"))


def test_profile_update_trims_and_only_touches_sent_fields(api, db):
    api.patch("/settings/user-profile", headers=STAFF, json={"rank": " Sgt ", "car_reg": "AB12 CDE"})
    api.patch("/settings/user-profile", headers=STAFF, json={"initials": "S"})
    body = api.get("/settings/user-profile", headers=STAFF).json()
    assert (body["rank"], body["car_reg"], body["initials"]) == ("Sgt", "AB12 CDE", "S")


def test_names_follow_google_not_the_profile_patch(api):
    # The User name is re-synced from the Google token on every request, so a
    # PATCHed name only lasts until the next call. The UI doesn't offer name
    # edits; this pins that Google stays the source of truth.
    api.patch("/settings/user-profile", headers=STAFF, json={"first_name": "Samuel"})
    assert api.get("/settings/user-profile", headers=STAFF).json()["first_name"] == "Sam"


def test_bank_details_encrypted_and_clearable(api, db):
    api.patch("/settings/user-profile", headers=STAFF, json={
        "bank_account_name": " S Staff ", "bank_sort_code": "12-34-56", "bank_account_number": "12345678"})
    profile = db.query(UserProfile).one()
    assert profile.bank_sort_code != "12-34-56"
    body = api.get("/settings/user-profile", headers=STAFF).json()
    assert (body["bank_account_name"], body["bank_sort_code"], body["bank_account_number"]) == (
        "S Staff", "12-34-56", "12345678")

    api.patch("/settings/user-profile", headers=STAFF, json={"bank_sort_code": "  "})
    db.refresh(profile)
    assert profile.bank_sort_code is None and profile.bank_account_number is not None


def test_undecryptable_bank_details_read_as_empty(api, db):
    api.get("/settings/user-profile", headers=STAFF)
    db.add(UserProfile(user_id=_user(db, "staff@317atc.co.uk").id, bank_account_name="corrupt"))
    db.commit()
    assert api.get("/settings/user-profile", headers=STAFF).json()["bank_account_name"] == ""


def test_profile_rejects_wrong_types(api):
    assert api.patch("/settings/user-profile", json={"rank": 5}, headers=STAFF).status_code == 422


# ── assessor name ─────────────────────────────────────────────────────────────

def test_assessor_name(api):
    assert api.get("/settings/assessor-name", headers=NCO).json() == {"assessor_name": ""}
    api.patch("/settings/assessor-name", json={"assessor_name": "  Cpl N Corporal "}, headers=NCO)
    assert api.get("/settings/assessor-name", headers=NCO).json() == {"assessor_name": "Cpl N Corporal"}
    assert api.patch("/settings/assessor-name", json={}, headers=NCO).status_code == 422
