"""record_audit: writes one row per call, and never takes the caller down."""

import pytest

import core.audit as audit
from database.models import AuditEvent

STAFF = {"email": "staff@317atc.co.uk"}


@pytest.fixture(autouse=True)
def _role(monkeypatch):
    monkeypatch.setattr(audit, "get_user_role", lambda email: "staff")


def test_a_row_carries_actor_role_action_and_detail(db):
    audit.record_audit(db, STAFF, action="read", resource="cadet_medical", resource_id=7,
                       detail={"cadet_count": 3})
    row = db.query(AuditEvent).one()
    assert (row.actor_email, row.actor_role, row.action, row.resource) == (
        "staff@317atc.co.uk", "staff", "read", "cadet_medical")
    assert row.resource_id == "7" and row.detail == {"cadet_count": 3}
    assert row.occurred_at is not None


def test_no_resource_id_is_stored_as_null(db):
    audit.record_audit(db, STAFF, action="run", resource="db_backup")
    assert db.query(AuditEvent).one().resource_id is None


def test_a_failed_insert_is_swallowed_and_the_session_stays_usable(db, monkeypatch):
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "commit", boom)
    audit.record_audit(db, STAFF, action="read", resource="cadet_medical")  # must not raise
    monkeypatch.undo()
    # Without the rollback the session would be stuck on the failed transaction.
    assert db.query(AuditEvent).count() == 0
    audit.record_audit(db, STAFF, action="read", resource="cadet_medical")
    assert db.query(AuditEvent).count() == 1


def test_a_role_lookup_failure_does_not_break_the_caller(db, monkeypatch):
    def boom(email):
        raise RuntimeError("Google down")

    monkeypatch.setattr(audit, "get_user_role", boom)
    # The role is looked up inside the guarded block, so an outage there must
    # not take a medical-records page down either.
    audit.record_audit(db, STAFF, action="read", resource="cadet_medical")
    assert db.query(AuditEvent).count() == 0
