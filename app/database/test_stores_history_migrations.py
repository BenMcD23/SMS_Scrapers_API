"""The issuance history migration builds the table the model expects, copies
in every existing "last issued" record, and undoes cleanly."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import StoresIssuanceHistory

MIGRATION = Path(__file__).parent / "alembic/versions/i1s2h3i4s5t6_add_stores_issuance_history.py"


def _run(engine, step):
    spec = importlib.util.spec_from_file_location("issuance_history_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(migration, step)()


def test_upgrade_matches_the_model_backfills_every_record_and_downgrade_removes_it():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "Cadets" (cin BIGINT PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE "Users" (id INTEGER PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE "Stores_Order_Items" (id INTEGER PRIMARY KEY)'))
        conn.execute(sa.text(
            'CREATE TABLE "Stores_Item_Issuances" (id INTEGER PRIMARY KEY, cadet_id BIGINT, user_id INTEGER, '
            'item_category TEXT NOT NULL, last_given DATETIME NOT NULL, size_given TEXT)'))
        conn.execute(sa.text(
            "INSERT INTO \"Stores_Item_Issuances\" (cadet_id, user_id, item_category, last_given, size_given) VALUES "
            "(1001, NULL, 'Beret', '2025-03-01 00:00:00', '55'), "
            "(NULL, 7, 'Tie', '2024-09-01 00:00:00', NULL)"))

    _run(engine, "upgrade")
    assert {c["name"] for c in sa.inspect(engine).get_columns("Stores_Issuance_History")} == \
        {c.name for c in StoresIssuanceHistory.__table__.columns}
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            'SELECT cadet_id, user_id, item_category, size_given, recorded_by, undone_at '
            'FROM "Stores_Issuance_History" ORDER BY id')).all()
    # Oldest first, so the newest entry is still the one that is current.
    assert rows == [(None, 7, "Tie", None, None, None), (1001, None, "Beret", "55", None, None)]

    _run(engine, "downgrade")
    assert "Stores_Issuance_History" not in sa.inspect(engine).get_table_names()


def test_stock_changes_and_deleted_records_migration_matches_the_models():
    from database.models import StoresDeletedRecord, StoresStockChange

    migration = Path(__file__).parent / "alembic/versions/s1t2o3r4e5h6_stock_changes_and_deleted_records.py"
    spec = importlib.util.spec_from_file_location("stores_history_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()
    insp = sa.inspect(engine)
    for model in (StoresStockChange, StoresDeletedRecord):
        assert {c["name"] for c in insp.get_columns(model.__tablename__)} == {c.name for c in model.__table__.columns}
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.downgrade()
    assert not sa.inspect(engine).get_table_names()
