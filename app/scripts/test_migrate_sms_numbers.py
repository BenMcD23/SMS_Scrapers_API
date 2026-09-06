"""The one-off move of the old text list onto cadet and staff records.

This runs against production once, so the things worth pinning are the ones that
would be discovered too late: that a dry run really writes nothing, that a name
it can't place stays on the list rather than vanishing, and that running it a
second time is uneventful.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, SmsRecipient, Staff
from scripts.migrate_sms_numbers import migrate


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def squadron(db):
    """A cadet, a CFAV, and an old list holding a number for each plus a parent
    and one name that could be either of two people."""
    db.add_all([
        Cadet(cin=1, first_name="Ada", last_name="Adams", rank="Corporal"),
        Cadet(cin=2, first_name="Sam", last_name="Smith", rank="Sergeant"),
        Staff(cin=10, first_name="Zoe", last_name="Zephyr", rank="Flight Lieutenant"),
        Staff(cin=11, first_name="Sid", last_name="Smith", rank="Sergeant"),
        SmsRecipient(rank="Cpl", surname="Adams", phone_number="07700900001"),
        SmsRecipient(rank="Flt Lt", surname="Zephyr", phone_number="+44 7700 900010"),
        SmsRecipient(rank="Sgt", surname="Smith", phone_number="07700900002"),
        SmsRecipient(rank="Mrs", surname="Mercer", phone_number="07700900020"),
    ])
    db.commit()
    return db


def numbers(db):
    return {
        **{c.last_name: c.phone_number for c in db.query(Cadet).all()},
        **{s.last_name: s.phone_number for s in db.query(Staff).all()},
    }


def test_a_dry_run_reports_everything_and_writes_nothing(squadron):
    result = migrate(squadron, apply=False)

    assert len(result["moved"]) == 2
    assert numbers(squadron) == {"Adams": None, "Smith": None, "Zephyr": None}
    assert squadron.query(SmsRecipient).count() == 4


def test_applying_puts_each_number_on_its_person_and_clears_the_row(squadron):
    migrate(squadron, apply=True)

    assert numbers(squadron)["Adams"] == "07700900001"
    # Stored the one way, whichever way it was written on the old list.
    assert numbers(squadron)["Zephyr"] == "07700900010"

    left = {r.surname for r in squadron.query(SmsRecipient).all()}
    assert left == {"Smith", "Mercer"}


def test_a_name_that_could_be_two_people_is_left_alone(squadron):
    result = migrate(squadron, apply=True)

    smith = next(u for u in result["unmatched"] if u[0] == "Sgt Smith")
    assert "share that surname" in smith[1]
    # Neither Smith was given the number, and the row still gets the texts.
    assert numbers(squadron)["Smith"] is None
    assert squadron.query(SmsRecipient).filter_by(surname="Smith").count() == 1


def test_someone_with_no_account_stays_on_the_list(squadron):
    migrate(squadron, apply=True)
    mercer = squadron.query(SmsRecipient).filter_by(surname="Mercer").one()
    assert mercer.phone_number == "07700900020"


def test_running_it_again_changes_nothing(squadron):
    migrate(squadron, apply=True)
    before = numbers(squadron)

    second = migrate(squadron, apply=True)

    assert second["moved"] == []
    assert numbers(squadron) == before
    assert squadron.query(SmsRecipient).count() == 2


def test_a_number_already_on_the_right_person_is_counted_not_rewritten(db):
    db.add_all([
        Cadet(cin=1, first_name="Ada", last_name="Adams", rank="Corporal",
              phone_number="07700900001"),
        SmsRecipient(rank="Cpl", surname="Adams", phone_number="07700 900 001"),
    ])
    db.commit()

    result = migrate(db, apply=True)

    assert result["moved"] == []
    assert len(result["already"]) == 1
    assert db.query(SmsRecipient).count() == 0


def test_a_number_notify_could_never_text_is_reported_and_kept(db):
    db.add_all([
        Cadet(cin=1, first_name="Ada", last_name="Adams", rank="Corporal"),
        SmsRecipient(rank="Cpl", surname="Adams", phone_number="0161 496 0000"),
    ])
    db.commit()

    result = migrate(db, apply=True)

    assert [name for name, _ in result["invalid"]] == ["Cpl Adams"]
    assert db.query(Cadet).one().phone_number is None
    assert db.query(SmsRecipient).count() == 1


def test_keep_rows_moves_the_numbers_without_emptying_the_old_list(squadron):
    migrate(squadron, apply=True, keep_rows=True)

    assert numbers(squadron)["Adams"] == "07700900001"
    assert squadron.query(SmsRecipient).count() == 4
