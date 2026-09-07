"""The recipient list and the name matching the migration leans on.

Both read from a real (in-memory) database, because what's under test is which
rows are picked up and how two sources are reconciled, not any one function.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, SmsRecipient, Staff
from texts.matching import Roster
from texts.recipients import list_recipients, parse_key


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def cadet(db, cin, first, last, rank=None, phone=None, email=None):
    row = Cadet(cin=cin, first_name=first, last_name=last, rank=rank,
                phone_number=phone, email=email)
    db.add(row)
    db.commit()
    return row


def staff(db, cin, first, last, rank=None, phone=None, email=None):
    row = Staff(cin=cin, first_name=first, last_name=last, rank=rank,
                phone_number=phone, email=email)
    db.add(row)
    db.commit()
    return row


def extra(db, rank, surname, phone):
    row = SmsRecipient(rank=rank, surname=surname, phone_number=phone)
    db.add(row)
    db.commit()
    return row


# ── The list ──────────────────────────────────────────────────────────────────

def test_only_people_with_a_number_are_on_the_list(db):
    cadet(db, 1, "Ada", "Adams", "Corporal", phone="07700900001")
    cadet(db, 2, "Bo", "Brown", "Cadet")           # no number
    cadet(db, 3, "Cy", "Clark", "Cadet", phone="")  # blank, not None

    assert [r.name for r in list_recipients(db)] == ["Ada Adams"]


def test_cadets_staff_and_extras_all_land_on_one_list_in_surname_order(db):
    staff(db, 10, "Zoe", "Zephyr", "Flight Lieutenant", phone="07700900010")
    cadet(db, 1, "Ada", "Adams", "Corporal", phone="07700900001")
    extra(db, "Mrs", "Mercer", "07700900020")

    assert [(r.source, r.surname) for r in list_recipients(db)] == [
        ("cadet", "Adams"), ("extra", "Mercer"), ("staff", "Zephyr"),
    ]


def test_a_cadet_with_no_rank_is_still_greeted_as_one(db):
    cadet(db, 1, "Ada", "Adams", None, phone="07700900001")
    assert list_recipients(db)[0].rank == "Cdt"


def test_the_greeting_shortens_the_rank_the_roster_spells_out(db):
    cadet(db, 1, "Ada", "Adams", "Corporal", phone="07700900001")
    staff(db, 10, "Zoe", "Zephyr", "Flight Lieutenant", phone="07700900010")

    assert [r.rank for r in list_recipients(db)] == ["Cpl", "Flt Lt"]


def test_a_rank_we_do_not_know_is_left_as_written(db):
    staff(db, 10, "Zoe", "Zephyr", "Padre", phone="07700900010")
    assert list_recipients(db)[0].rank == "Padre"


def test_one_number_is_texted_once_however_many_rows_hold_it(db):
    staff(db, 10, "Zoe", "Zephyr", "Flight Lieutenant", phone="07700900010")
    # Same person, still sat on the old list under a differently typed number.
    extra(db, "Flt Lt", "Zephyr", "+44 7700 900010")

    recipients = list_recipients(db)
    assert len(recipients) == 1
    # The account wins — it's the copy somebody keeps current.
    assert recipients[0].source == "staff"


def test_recipient_keys_round_trip(db):
    cadet(db, 1234567, "Ada", "Adams", "Corporal", phone="07700900001")
    key = list_recipients(db)[0].key
    assert key == "cadet:1234567"
    assert parse_key(key) == ("cadet", 1234567)


@pytest.mark.parametrize("key", ["", "cadet", "cadet:", "nobody:1", "cadet:abc"])
def test_a_key_that_names_nothing_is_refused(key):
    with pytest.raises(ValueError):
        parse_key(key)


# ── Matching a rank and surname to a person ───────────────────────────────────

def test_a_unique_surname_matches_whatever_the_rank_says(db):
    person = cadet(db, 1, "Ada", "Adams", "Corporal")
    match = Roster(db).match("Cpl", "Adams")
    assert (match.kind, match.person) == ("cadet", person)


def test_surnames_match_through_punctuation_and_case(db):
    person = staff(db, 10, "Owen", "O'Brien", "Civilian Instructor")
    assert Roster(db).match("CI", "obrien").person is person


def test_an_abbreviated_rank_is_read_as_the_full_one(db):
    cadet(db, 1, "Ada", "Smith", "Corporal")
    sergeant = cadet(db, 2, "Bo", "Smith", "Sergeant")
    assert Roster(db).match("Sgt", "Smith").person is sergeant


def test_a_staff_only_rank_picks_the_staff_member_over_the_cadet(db):
    cadet(db, 1, "Ada", "Smith", "Corporal")
    ci = staff(db, 10, "Bo", "Smith", "Civilian Instructor")
    match = Roster(db).match("CI", "Smith")
    assert (match.kind, match.person) == ("staff", ci)


def test_a_rank_both_rosters_hold_is_not_used_to_guess(db):
    # A cadet Sgt and a staff Sgt with the same surname: unresolvable from a
    # rank and a surname, and texting the wrong one is worse than reporting it.
    cadet(db, 1, "Ada", "Smith", "Sergeant")
    staff(db, 10, "Bo", "Smith", "Sergeant")
    match = Roster(db).match("Sgt", "Smith")
    assert not match.matched
    assert len(match.candidates) == 2


def test_a_surname_nobody_has_is_reported_not_guessed(db):
    cadet(db, 1, "Ada", "Adams", "Corporal")
    match = Roster(db).match("Cpl", "Nobody")
    assert not match.matched
    assert "surname" in match.reason
