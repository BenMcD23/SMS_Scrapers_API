"""The appointment applications migration builds the table the model
describes, comes back out cleanly, and leaves Alembic with a single head."""

import importlib.util
import re
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import AppointmentApplication

VERSIONS = Path(__file__).parent / "alembic/versions"
MIGRATION = VERSIONS / "a2p3p4l5i6c7_appointment_applications.py"


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


def test_the_migration_history_has_one_head():
    # Two heads make the deploy's `alembic upgrade head` fail outright.
    revisions, parents = set(), set()
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        revisions.add(re.search(r"^revision[^=]*=\s*'([^']+)'", text, re.M).group(1))
        down = re.search(r"^down_revision[^=]*=\s*(.+)$", text, re.M).group(1)
        parents.update(re.findall(r"'([^']+)'", down))
    assert revisions - parents == {"a2p3p4l5i6c7"}
