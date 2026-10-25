"""The appointment applications migrations build the table the model
describes and come back out cleanly. The single head is pinned in
test_migration_graph.py."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import AppointmentApplication

VERSIONS = Path(__file__).parent / "alembic/versions"
CREATE = VERSIONS / "a2p3p4l5i6c7_appointment_applications.py"
MARKED_COMPLETE = VERSIONS / "c3o4m5p6l7e8_application_marked_complete.py"


def _run(engine, migration, step):
    spec = importlib.util.spec_from_file_location(migration.stem, migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def _columns(engine, table):
    return {c["name"] for c in sa.inspect(engine).get_columns(table.name)}


def test_upgrade_matches_the_model_and_downgrade_removes_it():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "Cadets" (cin BIGINT PRIMARY KEY)'))

    _run(engine, CREATE, "upgrade")
    _run(engine, MARKED_COMPLETE, "upgrade")
    table = AppointmentApplication.__table__
    assert _columns(engine, table) == {c.name for c in table.columns}

    _run(engine, MARKED_COMPLETE, "downgrade")
    assert _columns(engine, table) == {c.name for c in table.columns} - {"completed_at", "completed_by"}
    _run(engine, CREATE, "downgrade")
    assert not sa.inspect(engine).has_table(table.name)


def test_marking_complete_keeps_the_applications_already_there():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "Cadets" (cin BIGINT PRIMARY KEY)'))
    _run(engine, CREATE, "upgrade")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO \"Appointment_Applications\" (kind, applicant_name, route, steps, started_at) "
            "VALUES ('uniformed', 'Jo Bloggs', 'snco', '{}', '2026-10-24 19:30:00')"))

    _run(engine, MARKED_COMPLETE, "upgrade")
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            'SELECT applicant_name, completed_at FROM "Appointment_Applications"')).one()
    # Nothing already tracked is suddenly complete.
    assert tuple(row) == ("Jo Bloggs", None)

