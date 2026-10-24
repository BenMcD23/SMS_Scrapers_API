"""The activity log migrations build the same tables the models describe, and
come back out cleanly."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import ActivityLog, ActivityLogEnrolment, ActivityLogEntry

VERSIONS = Path(__file__).parent / "alembic/versions"
# In upgrade order: the tables, then the sign-off columns on entries.
MIGRATIONS = [VERSIONS / "a1c2t3l4o5g6_activity_logs.py", VERSIONS / "s2i3g4n5o6f7_activity_log_entry_sign_off.py"]
TABLES = (ActivityLogEnrolment, ActivityLog, ActivityLogEntry)


def _run(engine, step, migration):
    spec = importlib.util.spec_from_file_location(migration.stem, migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def test_upgrade_matches_the_models_and_downgrade_removes_them():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "Cadets" (cin BIGINT PRIMARY KEY)'))

    for migration in MIGRATIONS:
        _run(engine, "upgrade", migration)
    insp = sa.inspect(engine)
    for model in TABLES:
        table = model.__table__
        assert {c["name"] for c in insp.get_columns(table.name)} == {c.name for c in table.columns}

    for migration in reversed(MIGRATIONS):
        _run(engine, "downgrade", migration)
    assert not any(sa.inspect(engine).has_table(m.__table__.name) for m in TABLES)
