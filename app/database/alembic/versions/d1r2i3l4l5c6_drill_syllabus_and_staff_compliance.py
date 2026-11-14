"""drill syllabus tracker and staff compliance from the Volunteer Portal

Drill_Stages / Drill_Lessons hold the squadron's progressive drill syllabus,
which staff edit in the app; it starts with the list below (ACP 19, the same
as core/drill.STARTER_SYLLABUS, copied so this migration never changes when
that does). Cadet_Drill_Lessons and Cadet_Drill_Tests are each cadet's record.

Staff_Portal_Data keeps the portal's DBS, BPSS and mandatory training for each
staff member; Staff_Compliance_Alerts stops the weekly email repeating itself.

Revision ID: d1r2i3l4l5c6
Revises: n1a2v3p4i5n6
Create Date: 2026-11-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1r2i3l4l5c6'
down_revision: Union[str, Sequence[str], None] = 'n1a2v3p4i5n6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STARTER_TEST = "5x5 drill test"
STARTER_MAX_SCORE = 25
STARTER_SYLLABUS = (
    ("Stage 1: At the halt", (
        "Attention, stand at ease and stand easy",
        "Turns at the halt",
        "Saluting at the halt",
        "Dismissing and falling out",
    )),
    ("Stage 2: Squad drill at the halt", (
        "Forming up in three ranks",
        "Dressing and numbering",
        "Open and close order",
        "Sizing a squad",
    )),
    ("Stage 3: On the march", (
        "Quick march and halt",
        "Turns on the march",
        "Wheeling",
        "Saluting on the march",
        "Eyes right and eyes left",
    )),
    ("Stage 4: Advanced", (
        "Changing step",
        "Marking time",
        "Words of command",
        "Taking a squad",
    )),
)


def upgrade() -> None:
    stages = op.create_table(
        'Drill_Stages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('test_name', sa.Text(), nullable=True),
        sa.Column('max_score', sa.Integer(), nullable=True),
        sa.Column('pass_mark', sa.Integer(), nullable=True),
    )
    lessons = op.create_table(
        'Drill_Lessons',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('stage_id', sa.Integer(), sa.ForeignKey('Drill_Stages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
    )
    op.create_index('ix_Drill_Lessons_stage_id', 'Drill_Lessons', ['stage_id'])

    op.create_table(
        'Cadet_Drill_Lessons',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cadet_id', sa.BigInteger(), sa.ForeignKey('Cadets.cin', ondelete='CASCADE'), nullable=False),
        sa.Column('lesson_id', sa.Integer(), sa.ForeignKey('Drill_Lessons.id', ondelete='CASCADE'), nullable=False),
        sa.Column('taught_at', sa.DateTime(), nullable=True),
        sa.Column('taught_by', sa.Text(), nullable=True),
        sa.Column('passed_at', sa.DateTime(), nullable=True),
        sa.Column('passed_by', sa.Text(), nullable=True),
        sa.UniqueConstraint('cadet_id', 'lesson_id', name='uq_drill_cadet_lesson'),
    )
    op.create_index('ix_Cadet_Drill_Lessons_cadet_id', 'Cadet_Drill_Lessons', ['cadet_id'])
    op.create_index('ix_Cadet_Drill_Lessons_lesson_id', 'Cadet_Drill_Lessons', ['lesson_id'])

    op.create_table(
        'Cadet_Drill_Tests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cadet_id', sa.BigInteger(), sa.ForeignKey('Cadets.cin', ondelete='CASCADE'), nullable=False),
        sa.Column('stage_id', sa.Integer(), sa.ForeignKey('Drill_Stages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tested_on', sa.Date(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('tested_by', sa.Text(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_Cadet_Drill_Tests_cadet_id', 'Cadet_Drill_Tests', ['cadet_id'])
    op.create_index('ix_Cadet_Drill_Tests_stage_id', 'Cadet_Drill_Tests', ['stage_id'])

    op.create_table(
        'Staff_Portal_Data',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('staff_id', sa.BigInteger(), sa.ForeignKey('Staff.cin', ondelete='CASCADE'), nullable=False),
        sa.Column('dataset', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('staff_id', 'dataset'),
    )
    op.create_index('ix_Staff_Portal_Data_staff_id', 'Staff_Portal_Data', ['staff_id'])

    op.create_table(
        'Staff_Compliance_Alerts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('staff_id', sa.BigInteger(), sa.ForeignKey('Staff.cin', ondelete='CASCADE'), nullable=False),
        sa.Column('item', sa.Text(), nullable=False),
        sa.Column('expires_on', sa.Date(), nullable=False),
        sa.Column('stage', sa.Text(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('staff_id', 'item', 'expires_on', 'stage'),
    )
    op.create_index('ix_Staff_Compliance_Alerts_staff_id', 'Staff_Compliance_Alerts', ['staff_id'])

    # The starter syllabus, with explicit ids so the lessons can point at their
    # stage without reading anything back; then move the sequences past them.
    op.bulk_insert(stages, [
        {"id": i + 1, "name": name, "position": i, "test_name": STARTER_TEST,
         "max_score": STARTER_MAX_SCORE, "pass_mark": None}
        for i, (name, _) in enumerate(STARTER_SYLLABUS)
    ])
    rows, next_id = [], 1
    for i, (_, names) in enumerate(STARTER_SYLLABUS):
        for j, name in enumerate(names):
            rows.append({"id": next_id, "stage_id": i + 1, "name": name, "position": j})
            next_id += 1
    op.bulk_insert(lessons, rows)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""SELECT setval(pg_get_serial_sequence('"Drill_Stages"', 'id'), (SELECT MAX(id) FROM "Drill_Stages"))""")
        op.execute("""SELECT setval(pg_get_serial_sequence('"Drill_Lessons"', 'id'), (SELECT MAX(id) FROM "Drill_Lessons"))""")


def downgrade() -> None:
    op.drop_table('Staff_Compliance_Alerts')
    op.drop_table('Staff_Portal_Data')
    op.drop_table('Cadet_Drill_Tests')
    op.drop_table('Cadet_Drill_Lessons')
    op.drop_table('Drill_Lessons')
    op.drop_table('Drill_Stages')
