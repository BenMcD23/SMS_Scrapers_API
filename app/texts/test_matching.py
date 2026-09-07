"""Reading a name off the old text list.

The rows the squadron actually had are the interesting ones: full names where a
surname was expected, two-word surnames that mustn't be split, ranks written
with dots, and the names that genuinely belong to nobody.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, Staff
from texts.matching import Roster, expand_rank, split_name


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def roster(db):
    db.add_all([
        Cadet(cin=1, first_name="Charlie", last_name="Wright", rank="Cadet"),
        Cadet(cin=2, first_name="Tyrese", last_name="Wright", rank="Cadet"),
        Cadet(cin=3, first_name="Aiman", last_name="Shahbaz", rank="Cadet"),
        Cadet(cin=4, first_name="Iman", last_name="Shahbaz", rank="Corporal"),
        Cadet(cin=5, first_name="Hannah", last_name="Boxall", rank="Corporal"),
        Cadet(cin=6, first_name="Elianna", last_name="Tyrell", rank="Sergeant"),
        Staff(cin=10, first_name="Graham", last_name="Boxall", rank="CI (Probationer)"),
        Staff(cin=11, first_name="Jacob", last_name="Gill", rank="CI"),
        Staff(cin=12, first_name="Joseph", last_name="Gill", rank="FS"),
        Staff(cin=13, first_name="Gareth", last_name="Lloyd Morris", rank="Sergeant"),
    ])
    db.commit()
    return Roster(db)


def test_a_full_name_finds_the_right_one_of_two_surnames(roster):
    match = roster.match("Cdt", "Charlie Wright")

    assert match.matched
    assert match.cin == 1
    assert match.reason == "first name and surname"


def test_a_two_word_surname_is_not_read_as_a_first_name(roster):
    """Gareth Lloyd Morris, not a Mr Morris called Lloyd."""
    match = roster.match("Sgt", "Lloyd Morris")

    assert match.cin == 13
    assert match.reason == "surname"


def test_a_surname_on_its_own_still_matches(roster):
    assert roster.match("Sgt", "Tyrell").cin == 6


def test_a_rank_written_with_dots_picks_the_staff_member(roster):
    """"C.I Boxall" is Graham; the other Boxall is a cadet corporal."""
    match = roster.match("C.I", "Boxall")

    assert match.kind == "staff"
    assert match.cin == 10


def test_a_dotted_rank_separates_two_staff_with_one_surname(roster):
    assert roster.match("C.I", "Gill").cin == 11
    assert roster.match("FS", "Gill").cin == 12


def test_a_first_initial_is_enough_when_it_only_fits_one(roster):
    assert roster.match("Cdt", "T Wright").cin == 2


def test_a_name_nobody_has_is_reported_rather_than_guessed(roster):
    match = roster.match("Cdt", "Griffin")

    assert not match.matched
    assert match.reason == "nobody on either roster has that surname"


def test_a_first_name_nobody_has_is_not_given_to_the_one_who_shares_a_surname(roster):
    """A Vanessa Tyrell where the roster has only an Elianna is a different
    person — her number must not land on Elianna's record."""
    match = roster.match("Sgt", "Vanessa Tyrell")

    assert not match.matched
    assert match.reason == "nobody with that surname is called that"


def test_a_surname_two_people_share_is_left_for_a_person_to_judge(roster):
    match = roster.match("Cdt", "Wright")

    assert not match.matched
    assert "share that surname" in match.reason
    assert len(match.candidates) == 2


@pytest.mark.parametrize("written, expected", [
    ("C.I", "civilian instructor"),
    ("C.I.", "civilian instructor"),
    ("CI (Probationer)", "civilian instructor"),
    ("Flt Lt", "flight lieutenant"),
    ("Cdt", "cadet"),
    ("ASgt", "asgt"),
])
def test_ranks_are_read_however_they_are_written(written, expected):
    assert expand_rank(written) == expected


@pytest.mark.parametrize("name, expected", [
    ("Shahbaz", ("", "Shahbaz")),
    ("Aiman Shahbaz", ("Aiman", "Shahbaz")),
    ("Mary Jane Watson", ("Mary Jane", "Watson")),
    ("", ("", "")),
])
def test_split_name_takes_the_last_word_as_the_surname(name, expected):
    assert split_name(name) == expected
