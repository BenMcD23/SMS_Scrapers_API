"""The migration that renames item type "Skirts" to "Skirt" must keep every row."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).parent / "alembic/versions/v1s2k3i4r5t6_rename_skirts_to_skirt.py"
TABLES = ("Stores_Items", "Stores_Order_Items", "Logs_Form_Entries")


def _upgrade(engine):
    spec = importlib.util.spec_from_file_location("skirt_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()


def test_skirts_become_skirt_and_no_row_is_lost_or_touched():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for t in TABLES:
            conn.execute(sa.text(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY, item_type TEXT, size TEXT)'))
            conn.execute(sa.text(
                f'INSERT INTO "{t}" (item_type, size) VALUES '
                "('Skirts', '1'), ('Skirt', '2'), ('Skirts', '2'), ('Slacks', '1')"
            ))

    _upgrade(engine)
    _upgrade(engine)  # re-running is harmless

    with engine.connect() as conn:
        for t in TABLES:
            rows = conn.execute(sa.text(f'SELECT item_type, size FROM "{t}" ORDER BY id')).fetchall()
            assert [tuple(r) for r in rows] == [("Skirt", "1"), ("Skirt", "2"), ("Skirt", "2"), ("Slacks", "1")]
