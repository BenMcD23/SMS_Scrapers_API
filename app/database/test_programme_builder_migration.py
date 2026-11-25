"""The programme builder migration builds exactly the tables the models
describe, and takes them away again."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

import database.models  # noqa: F401  (registers every table on Base)
from database.database import Base

MIGRATION = Path(__file__).parent / "alembic/versions/p7b8u9i0l1d2_add_programme_builder.py"
TABLES = {"Programme_Rooms", "Programme_Bank_Items", "Programme_Drafts", "Programme_Draft_Nights",
          "Programme_Slots", "Programme_Comments", "Programme_Changes"}


def _migration():
    spec = importlib.util.spec_from_file_location("programme_builder_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(_migration(), step)()


def _before(engine):
    """The schema the migration starts from: every other table."""
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in TABLES])


def test_upgrade_matches_the_models():
    engine = sa.create_engine("sqlite://")
    _before(engine)
    _run(engine, "upgrade")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    def ours(op) -> bool:
        # Entries are tuples, or lists of tuples for column changes; each
        # names its table somewhere inside, so match on the text.
        return any(f"'{t}'" in repr(op) or f"<{t}>" in repr(op) for t in TABLES)

    assert [d for d in diff if ours(d)] == []


def test_downgrade_removes_every_table():
    engine = sa.create_engine("sqlite://")
    _before(engine)
    _run(engine, "upgrade")
    assert TABLES <= set(sa.inspect(engine).get_table_names())
    _run(engine, "downgrade")
    assert TABLES & set(sa.inspect(engine).get_table_names()) == set()
