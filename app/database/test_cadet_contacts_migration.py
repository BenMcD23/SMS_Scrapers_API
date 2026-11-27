"""The migration adding cadets' Bader contact methods and their contacts."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).parent / "alembic/versions/c0n1t2a3c4t5_add_cadet_contacts.py"


def _migration():
    spec = importlib.util.spec_from_file_location("cadet_contacts_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(_migration(), step)()


def _engine():
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    sa.Table("Cadets", meta, sa.Column("cin", sa.BigInteger, primary_key=True), sa.Column("first_name", sa.Text))
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO "Cadets" (cin, first_name) VALUES (1, \'Amy\')'))
    return engine


def test_existing_cadets_start_unchecked_and_contacts_can_be_stored():
    engine = _engine()
    _run(engine, "upgrade")
    with engine.begin() as conn:
        assert conn.execute(sa.text(
            'SELECT contact_methods, contacts_checked_at FROM "Cadets"')).one() == (None, None)
        conn.execute(sa.text(
            'INSERT INTO "Cadet_Contacts" (cadet_id, position, name, relationship, next_of_kin, contact_methods) '
            "VALUES (1, 0, 'Sarah Able', 'Mother', 1, '[]')"))
        assert conn.execute(sa.text('SELECT name, relationship FROM "Cadet_Contacts"')).one() == (
            "Sarah Able", "Mother")


def test_downgrading_removes_the_table_and_columns():
    engine = _engine()
    _run(engine, "upgrade")
    _run(engine, "downgrade")
    inspector = sa.inspect(engine)
    assert inspector.get_table_names() == ["Cadets"]
    assert {c["name"] for c in inspector.get_columns("Cadets")} == {"cin", "first_name"}
