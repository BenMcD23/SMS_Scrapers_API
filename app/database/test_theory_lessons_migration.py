"""The migration that adds theory lessons: a subject staff marked before lessons
were counted must still read as taught afterwards."""

import importlib.util
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).parent / "alembic/versions/t1h2e3o4r5y6_add_theory_lessons_and_qr_codes.py"


def _migration():
    spec = importlib.util.spec_from_file_location("theory_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(engine):
    with engine.begin() as conn:
        migration = _migration()
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()


def _old_schema():
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    sa.Table("Cadets", meta, sa.Column("cin", sa.BigInteger, primary_key=True))
    sa.Table(
        "Cadet_Theory_Progress", meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cadet_id", sa.BigInteger), sa.Column("lesson_key", sa.Text),
        sa.Column("completed_at", sa.DateTime), sa.Column("recorded_by", sa.Text),
    )
    meta.create_all(engine)
    return engine


def test_staff_marked_subjects_get_every_lesson_and_portal_passes_get_none():
    engine = _old_schema()
    when = datetime(2026, 9, 1)
    with engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO "Cadets" (cin) VALUES (1)'))
        conn.execute(sa.text(
            'INSERT INTO "Cadet_Theory_Progress" (cadet_id, lesson_key, completed_at, recorded_by) VALUES '
            "(1, 'acp_32_2', :w, 'staff@x'), (1, 'blue_radio', :w, NULL), (1, 'acp_33_2', :w, 'Volunteer Portal')"
        ), {"w": when})

    _upgrade(engine)

    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            'SELECT lesson_key, part, recorded_by FROM "Cadet_Theory_Lessons" ORDER BY lesson_key, part'
        )).fetchall()
        tables = sa.inspect(conn).get_table_names()
    assert [tuple(r) for r in rows] == [
        ("acp_32_2", 1, "staff@x"), ("acp_32_2", 2, "staff@x"), ("acp_32_2", 3, "staff@x"),
        ("blue_radio", 1, None),
    ]
    assert "Theory_Lesson_Codes" in tables


def test_running_it_again_changes_nothing():
    engine = _old_schema()
    with engine.begin() as conn:
        conn.execute(sa.text(
            'INSERT INTO "Cadet_Theory_Progress" (cadet_id, lesson_key, completed_at, recorded_by) '
            "VALUES (1, 'acp_32_2', '2026-09-01', 'staff@x')"
        ))
    _upgrade(engine)
    _upgrade(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.text('SELECT COUNT(*) FROM "Cadet_Theory_Lessons"')).scalar() == 3


def test_the_migrations_copy_of_the_three_lesson_subjects_matches_the_catalogue():
    # It is a snapshot on purpose; this catches it being wrong on the day.
    from core.theory_lessons import THEORY_LESSONS

    assert set(_migration().THREE_LESSON_SUBJECTS) == {s.key for s in THEORY_LESSONS if s.parts == 3}


DROP_CODES = Path(__file__).parent / "alembic/versions/u1q2r3c4o5d6_drop_theory_lesson_codes.py"


def _run(path, engine):
    spec = importlib.util.spec_from_file_location("drop_codes_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def test_the_codes_table_is_dropped_now_the_codes_live_in_source():
    engine = _old_schema()
    _upgrade(engine)
    _run(DROP_CODES, engine)
    with engine.connect() as conn:
        tables = sa.inspect(conn).get_table_names()
    assert "Theory_Lesson_Codes" not in tables
    assert "Cadet_Theory_Lessons" in tables


def test_dropping_it_again_or_where_it_never_existed_is_harmless():
    engine = _old_schema()
    _run(DROP_CODES, engine)
    _run(DROP_CODES, engine)
