from sqlalchemy import event

from core.db import get_or_create_user
from database.models import User

IDINFO = {"sub": "g-1", "email": "a@x", "given_name": "Ann", "family_name": "Lee"}


def _commits(db):
    seen = []
    event.listen(db, "after_commit", lambda s: seen.append(1))
    return seen


def test_first_sight_creates_the_user(db):
    user = get_or_create_user(db, IDINFO)
    assert (user.email, user.first_name, user.last_name) == ("a@x", "Ann", "Lee")
    assert db.query(User).count() == 1


def test_a_returning_user_with_the_same_name_costs_no_commit(db):
    # Most requests resolve the caller; a commit each time is a wasted round trip.
    get_or_create_user(db, IDINFO)
    commits = _commits(db)
    assert get_or_create_user(db, IDINFO).first_name == "Ann"
    assert commits == []


def test_a_changed_name_is_saved(db):
    get_or_create_user(db, IDINFO)
    commits = _commits(db)
    get_or_create_user(db, {**IDINFO, "family_name": "Smith"})
    assert commits == [1]
    db.expire_all()
    assert db.query(User).one().last_name == "Smith"


def test_a_token_without_names_keeps_the_stored_ones(db):
    get_or_create_user(db, IDINFO)
    user = get_or_create_user(db, {"sub": "g-1", "email": "a@x"})
    assert (user.first_name, user.last_name) == ("Ann", "Lee")
