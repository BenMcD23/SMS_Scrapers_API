"""The WhatsApp community invite link — the one setting the texts carry.

The validation is deliberately loose (scheme and domain, nothing more), so what
these pin is that it stays loose: rejecting a link WhatsApp actually issued
would leave staff unable to save something that works.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import TextSettings
from texts.settings import clean_invite_url, community_invite_url, get_text_settings


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_the_settings_row_makes_itself_on_first_read(db):
    assert db.query(TextSettings).count() == 0

    settings = get_text_settings(db)

    assert settings.whatsapp_invite_url == ""
    assert db.query(TextSettings).count() == 1


def test_reading_it_twice_does_not_make_a_second_row(db):
    first, second = get_text_settings(db), get_text_settings(db)

    assert first.id == second.id
    assert db.query(TextSettings).count() == 1


def test_no_link_set_reads_as_empty_not_none(db):
    # The portals show the join prompt on truthiness, so "" is the contract.
    assert community_invite_url(db) == ""


@pytest.mark.parametrize("url", [
    "https://chat.whatsapp.com/ABCDEFGHIJKLMNOPQRSTUV",
    # Whatever path shape WhatsApp moves to, and whichever subdomain.
    "https://chat.whatsapp.com/invite/ABCDEFGHIJKLMNOPQRSTUV",
    "https://whatsapp.com/anything",
])
def test_a_whatsapp_link_is_accepted_whatever_its_path(url):
    assert clean_invite_url(url) == url


def test_surrounding_whitespace_from_a_paste_is_trimmed():
    assert clean_invite_url("  https://chat.whatsapp.com/ABC  ") == "https://chat.whatsapp.com/ABC"


def test_clearing_the_link_is_allowed():
    assert clean_invite_url("") == ""
    assert clean_invite_url("   ") == ""


def test_an_insecure_link_is_refused():
    with pytest.raises(ValueError, match="https"):
        clean_invite_url("http://chat.whatsapp.com/ABC")


@pytest.mark.parametrize("url", [
    "https://chat.whatsapp.com.evil.test/ABC",  # domain suffix, not the domain
    "https://example.com/group",
    "chat.whatsapp.com/ABC",                    # no scheme at all
])
def test_something_that_is_not_a_whatsapp_link_is_refused(url):
    with pytest.raises(ValueError):
        clean_invite_url(url)
