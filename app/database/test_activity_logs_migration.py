"""The activity log migration builds the same tables the models describe, and
comes back out cleanly."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import ActivityLog, ActivityLogEnrolment, ActivityLogEntry

MIGRATION = Path(__file__).parent / "alembic/versions/a1c2t3l4o5g6_activity_logs.py"
TABLES = (ActivityLogEnrolment, ActivityLog, ActivityLogEntry)


def _run(engine, step):
    spec = importlib.util.spec_from_file_location("activity_logs_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def test_upgrade_matches_the_models_and_downgrade_removes_them():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "Cadets" (cin BIGINT PRIMARY KEY)'))

    _run(engine, "upgrade")
    insp = sa.inspect(engine)
    for model in TABLES:
        table = model.__table__
        assert {c["name"] for c in insp.get_columns(table.name)} == {c.name for c in table.columns}

    _run(engine, "downgrade")
    assert not any(sa.inspect(engine).has_table(m.__table__.name) for m in TABLES)
