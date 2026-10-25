"""The Push_Subscriptions migration builds the table the model expects, and
undoes cleanly."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import PushSubscription

MIGRATION = Path(__file__).parent / "alembic/versions/p1u2s3h4s5b6_add_push_subscriptions.py"


def _run(engine, step):
    spec = importlib.util.spec_from_file_location("push_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(migration, step)()


def test_upgrade_matches_the_model_and_downgrade_removes_it():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    inspector = sa.inspect(engine)
    assert {c["name"] for c in inspector.get_columns("Push_Subscriptions")} == \
        {c.name for c in PushSubscription.__table__.columns}
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO \"Push_Subscriptions\" (email, endpoint, p256dh, auth, topics, created_at) "
                             "VALUES ('a', 'https://e', 'k', 'a', '[]', '2026-01-01')"))
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO \"Push_Subscriptions\" (email, endpoint, p256dh, auth, topics, "
                             "created_at) VALUES ('b', 'https://e', 'k', 'a', '[]', '2026-01-01')"))

    _run(engine, "downgrade")
    assert "Push_Subscriptions" not in sa.inspect(engine).get_table_names()
