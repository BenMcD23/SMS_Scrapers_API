"""The appointment applications migration builds the table the model
describes and comes back out cleanly. The single head is pinned in
test_migration_graph.py."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import AppointmentApplication

MIGRATION = Path(__file__).parent / "alembic/versions" / "a2p3p4l5i6c7_appointment_applications.py"


def _run(engine, step):
    spec = importlib.util.spec_from_file_location("appointment_applications_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def test_upgrade_matches_the_model_and_downgrade_removes_it():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "Cadets" (cin BIGINT PRIMARY KEY)'))

    _run(engine, "upgrade")
    table = AppointmentApplication.__table__
    assert {c["name"] for c in sa.inspect(engine).get_columns(table.name)} == {c.name for c in table.columns}

    _run(engine, "downgrade")
    assert not sa.inspect(engine).has_table(table.name)

