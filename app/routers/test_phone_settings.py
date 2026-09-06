"""Saving a number, from either side, and being handed the community link.

The two entry points are deliberately different — a cadet is resolved from their
own Cadet row, a staff member or NCO from whichever roster their email is on —
so both are driven here, along with the invite link they answer with.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, Staff, TextSettings
import routers.portal as portal
import routers.settings as settings


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Ada", last_name="Adams", rank="Corporal",
              email="ada.adams@317atc.co.uk"),
        Staff(cin=10, first_name="Zoe", last_name="Zephyr", rank="Flight Lieutenant",
              email="zoe.zephyr@317atc.co.uk"),
    ])
    db.commit()
    return db


def with_link(db, url="https://chat.whatsapp.com/ABCDEFGH"):
    db.add(TextSettings(whatsapp_invite_url=url))
    db.commit()
    return url


def token(email):
    return {"sub": email, "email": email}


# ── The cadet portal ──────────────────────────────────────────────────────────

def test_a_cadet_saves_their_own_number(squadron):
    cadet = squadron.query(Cadet).one()

    result = portal.cadet_set_phone_number(
        portal.PhoneNumberBody(phone_number="+44 7700 900001"), db=squadron, cadet=cadet)

    assert result["phone_number"] == "07700900001"
    assert cadet.phone_number == "07700900001"


def test_saving_a_number_hands_back_the_community_link(squadron):
    url = with_link(squadron)
    cadet = squadron.query(Cadet).one()

    result = portal.cadet_set_phone_number(
        portal.PhoneNumberBody(phone_number="07700900001"), db=squadron, cadet=cadet)

    # The portal offers the community straight after the save, off this one call.
    assert result["whatsapp_invite_url"] == url


def test_no_link_configured_reads_as_empty_so_nothing_is_offered(squadron):
    cadet = squadron.query(Cadet).one()

    result = portal.cadet_get_me(db=squadron, cadet=cadet)

    assert result["whatsapp_invite_url"] == ""


def test_a_cadet_clearing_their_number_comes_off_the_list(squadron):
    cadet = squadron.query(Cadet).one()
    cadet.phone_number = "07700900001"
    squadron.commit()

    portal.cadet_set_phone_number(
        portal.PhoneNumberBody(phone_number=""), db=squadron, cadet=cadet)

    assert cadet.phone_number is None


def test_a_cadet_cannot_save_a_number_notify_could_not_text(squadron):
    cadet = squadron.query(Cadet).one()

    with pytest.raises(portal.HTTPException) as exc:
        portal.cadet_set_phone_number(
            portal.PhoneNumberBody(phone_number="0161 496 0000"), db=squadron, cadet=cadet)

    assert exc.value.status_code == 400
    assert cadet.phone_number is None


# ── The SMS site ──────────────────────────────────────────────────────────────

def test_a_staff_number_lands_on_their_roster_row(squadron):
    result = settings.update_phone_number(
        settings.PhoneNumberPatch(phone_number="07700900010"),
        db=squadron, idinfo=token("zoe.zephyr@317atc.co.uk"))

    assert result["kind"] == "staff"
    assert squadron.query(Staff).one().phone_number == "07700900010"


def test_an_nco_signing_in_here_saves_onto_their_cadet_row(squadron):
    # NCOs use the SMS site too, and their number belongs on the cadet roster.
    result = settings.update_phone_number(
        settings.PhoneNumberPatch(phone_number="07700900001"),
        db=squadron, idinfo=token("ada.adams@317atc.co.uk"))

    assert result["kind"] == "cadet"
    assert squadron.query(Cadet).one().phone_number == "07700900001"


def test_the_roster_row_is_matched_whatever_the_case_of_the_email(squadron):
    result = settings.get_phone_number(
        db=squadron, idinfo=token("Zoe.Zephyr@317ATC.co.uk"))

    assert result["kind"] == "staff"
    assert result["name"] == "Zoe Zephyr"


def test_an_account_on_neither_roster_is_told_rather_than_failing_quietly(squadron):
    with pytest.raises(settings.HTTPException) as exc:
        settings.update_phone_number(
            settings.PhoneNumberPatch(phone_number="07700900099"),
            db=squadron, idinfo=token("new.starter@317atc.co.uk"))

    assert exc.value.status_code == 404


def test_the_sms_site_gets_the_community_link_with_the_number(squadron):
    url = with_link(squadron)

    result = settings.update_phone_number(
        settings.PhoneNumberPatch(phone_number="07700900010"),
        db=squadron, idinfo=token("zoe.zephyr@317atc.co.uk"))

    assert result["whatsapp_invite_url"] == url
