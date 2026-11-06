"""The catalogue migration builds the tables the models expect, is safe to run
twice (some environments were created with create_all), and undoes cleanly."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from database.models import CatalogueBadgeCategory, CatalogueItemType

MIGRATION = Path(__file__).parent / "alembic/versions/c1a2t3a4l5o6_add_editable_catalogue.py"


def _run(engine, step):
    spec = importlib.util.spec_from_file_location("catalogue_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(migration, step)()


def test_upgrade_matches_the_models_and_downgrade_removes_them():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    inspector = sa.inspect(engine)
    for model in (CatalogueItemType, CatalogueBadgeCategory):
        table = model.__table__.name
        assert {c["name"] for c in inspector.get_columns(table)} == {c.name for c in model.__table__.columns}

    _run(engine, "downgrade")
    tables = sa.inspect(engine).get_table_names()
    assert "Catalogue_Item_Types" not in tables and "Catalogue_Badge_Categories" not in tables


def test_running_it_twice_changes_nothing():
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    _run(engine, "upgrade")
    assert "Catalogue_Item_Types" in sa.inspect(engine).get_table_names()


def test_the_database_refuses_a_duplicate_name_even_if_the_api_did_not():
    # Two first requests racing to seed rely on this to fail one of them.
    engine = sa.create_engine("sqlite://")
    _run(engine, "upgrade")
    insert = sa.text('INSERT INTO "Catalogue_Item_Types" (name) VALUES (:n)')
    with engine.begin() as conn:
        conn.execute(insert, {"n": "Beret"})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"n": "Beret"})
