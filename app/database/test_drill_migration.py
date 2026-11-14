"""The migration that adds the drill tracker: it starts the squadron off with the
same syllabus core/drill.py describes, each stage tested by a 5x5."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from core import drill

MIGRATION = Path(__file__).parent / "alembic/versions/d1r2i3l4l5c6_drill_syllabus_and_staff_compliance.py"


def _migration():
    spec = importlib.util.spec_from_file_location("drill_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgraded():
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    sa.Table("Cadets", meta, sa.Column("cin", sa.BigInteger, primary_key=True))
    sa.Table("Staff", meta, sa.Column("cin", sa.BigInteger, primary_key=True))
    meta.create_all(engine)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _migration().upgrade()
    return engine


def test_the_starter_syllabus_is_the_one_core_drill_describes():
    # The migration keeps its own copy so it never changes after it has run;
    # this catches the two drifting apart before the first deploy.
    m = _migration()
    assert m.STARTER_SYLLABUS == drill.STARTER_SYLLABUS
    assert (m.STARTER_TEST, m.STARTER_MAX_SCORE) == (drill.STARTER_TEST, drill.STARTER_MAX_SCORE)


def test_upgrading_writes_every_stage_and_lesson_in_order():
    engine = _upgraded()
    with engine.connect() as conn:
        stages = conn.execute(sa.text(
            'SELECT id, name, test_name, max_score FROM "Drill_Stages" ORDER BY position')).fetchall()
        lessons = conn.execute(sa.text(
            'SELECT stage_id, name FROM "Drill_Lessons" ORDER BY stage_id, position')).fetchall()
    assert [s.name for s in stages] == [name for name, _ in drill.STARTER_SYLLABUS]
    assert {(s.test_name, s.max_score) for s in stages} == {("5x5 drill test", 25)}
    by_stage = {s.id: s.name for s in stages}
    assert [(by_stage[sid], name) for sid, name in lessons] == [
        (stage, lesson) for stage, names in drill.STARTER_SYLLABUS for lesson in names
    ]


def test_a_stage_added_after_the_migration_gets_a_fresh_id():
    engine = _upgraded()
    with engine.begin() as conn:
        conn.execute(sa.text('INSERT INTO "Drill_Stages" (name, position) VALUES (\'Colour party\', 9)'))
        new_id = conn.execute(sa.text('SELECT id FROM "Drill_Stages" WHERE name = \'Colour party\'')).scalar()
    assert new_id == len(drill.STARTER_SYLLABUS) + 1


def test_downgrading_removes_every_new_table():
    engine = _upgraded()
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _migration().downgrade()
    with engine.connect() as conn:
        assert set(sa.inspect(conn).get_table_names()) == {"Cadets", "Staff"}
