"""The migration that indexes foreign keys, gives the cadet foreign keys a
delete rule and makes qualifications and event places one row each.

The schema before it is the models minus what it adds. The SQLite tests run in
CI; the PostgreSQL one (delete rules, and that the result matches the models
exactly) needs TEST_POSTGRES_URL, e.g.

    TEST_POSTGRES_URL=postgresql://postgres@localhost:5433/scratch pytest app/database/test_fk_indexes_migration.py
"""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

import database.models  # noqa: F401  (registers every table on Base)
from database.database import Base

MIGRATION = Path(__file__).parent / "alembic/versions/d1b2i3n4d5x6_fk_indexes_delete_rules_uniques.py"
PG_URL = os.environ.get("TEST_POSTGRES_URL")
OLD_INDEX_NAMES = {  # created by hand in earlier migrations, before the standard names
    ("NCO_Holidays", "user_id"): "ix_nco_holidays_user_id",
}


def _migration():
    spec = importlib.util.spec_from_file_location("fk_indexes_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step="upgrade"):
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(_migration(), step)()


def _old_metadata() -> sa.MetaData:
    """Today's tables as they were before the migration: none of the indexes
    or uniques it adds, and no delete rule on the foreign keys it changes."""
    m = _migration()
    added = set(m.INDEXED)
    uniques = {name for name, *_ in m.UNIQUES}
    rules = {(t, c) for t, c, *_ in m.DELETE_RULES}
    meta = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        t = table.to_metadata(meta)
        for ix in list(t.indexes):
            cols = [c.name for c in ix.columns]
            if len(cols) == 1 and (t.name, cols[0]) in added:
                t.indexes.discard(ix)
        for con in list(t.constraints):
            if isinstance(con, sa.UniqueConstraint) and con.name in uniques:
                t.constraints.discard(con)
        for col in t.columns:
            for fk in col.foreign_keys:
                if (t.name, col.name) in rules:
                    fk.ondelete = None
    for (table, column), name in OLD_INDEX_NAMES.items():
        sa.Index(name, meta.tables[table].c[column])
    return meta


def _old_db(url="sqlite://"):
    engine = sa.create_engine(url)
    meta = _old_metadata()
    meta.drop_all(engine)
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO "Cadets" (cin, first_name, last_name, banned) VALUES '
                             "(1, 'A', 'B', false), (2, 'C', 'D', false)"))
        conn.execute(sa.text(
            'INSERT INTO "Cadet_Qualifications" (cadet_id, qual_type, status, date_achieved) VALUES '
            "(1, 'First Aid', 'true', '2024-01-01'), (1, 'First Aid', 'true', '2025-06-01'),"
            "(1, 'First Aid', 'true', NULL), (2, 'Radio', 'true', NULL), (2, 'Radio', 'true', NULL)"
        ))
        conn.execute(sa.text('INSERT INTO "All_Events" (id, title) VALUES (1, \'Camp\')'))
        conn.execute(sa.text('INSERT INTO "Cadet_Events" (event_id, cadet_id) VALUES (1, 1), (1, 1), (1, 2)'))
    return engine


def _rows(engine, sql):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(sa.text(sql))]


def test_duplicates_go_keeping_the_latest_qualification_and_the_first_event_place():
    engine = _old_db()
    _run(engine)
    assert _rows(engine, 'SELECT cadet_id, qual_type, substr(date_achieved, 1, 10) '
                         'FROM "Cadet_Qualifications" ORDER BY cadet_id') == [
        (1, "First Aid", "2025-06-01"),  # the dated, latest row beats the undated one
        (2, "Radio", None),
    ]
    assert _rows(engine, 'SELECT id, cadet_id FROM "Cadet_Events" ORDER BY id') == [(1, 1), (3, 2)]


def test_a_second_copy_is_refused_afterwards():
    engine = _old_db()
    _run(engine)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO "Cadet_Events" (event_id, cadet_id) VALUES (1, 2)'))


def test_every_foreign_key_the_migration_lists_is_indexed_under_its_standard_name():
    engine = _old_db()
    _run(engine)
    insp = sa.inspect(engine)
    for table, column in _migration().INDEXED:
        names = {i["name"] for i in insp.get_indexes(table) if i["column_names"] == [column]}
        assert names == {f"ix_{table}_{column}"}, (table, column, names)


def test_it_runs_twice_without_harm_and_downgrades():
    # A half-applied run retried by the deploy must not fail on what's there.
    engine = _old_db()
    _run(engine)
    _run(engine, "downgrade")
    _run(engine)


def test_the_models_index_every_foreign_key():
    # Postgres doesn't index a foreign key for you: a new one needs index=True
    # (or a unique/index that leads with it), or its lookups scan the table.
    missing = []
    for table in Base.metadata.sorted_tables:
        leads = {list(i.columns)[0].name for i in table.indexes}
        leads |= {list(c.columns)[0].name for c in table.constraints
                  if isinstance(c, (sa.UniqueConstraint, sa.PrimaryKeyConstraint)) and len(c.columns)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.foreign_keys and c.name not in leads]
    assert missing == []


@pytest.mark.skipif(not PG_URL, reason="needs TEST_POSTGRES_URL (a real Postgres)")
def test_on_postgres_cadet_rows_follow_the_cadet_and_the_schema_matches_the_models():
    engine = _old_db(PG_URL)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text('INSERT INTO "Stores_Orders" (cadet_id, created_at, completed, kitting) '
                                 "VALUES (1, now(), false, false)"))
        _run(engine)
        with engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM "Cadets" WHERE cin = 1'))
        assert _rows(engine, 'SELECT count(*) FROM "Cadet_Qualifications" WHERE cadet_id = 1') == [(0,)]
        assert _rows(engine, 'SELECT count(*) FROM "Cadet_Events" WHERE cadet_id = 1') == [(0,)]
        assert _rows(engine, 'SELECT cadet_id FROM "Stores_Orders"') == [(None,)]  # the order outlives them

        with engine.connect() as conn:
            diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diffs == []
    finally:
        Base.metadata.drop_all(engine)
