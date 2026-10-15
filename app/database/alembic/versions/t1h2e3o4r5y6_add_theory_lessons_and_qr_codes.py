"""add theory lessons and their QR check-in codes

Revision ID: t1h2e3o4r5y6
Revises: m1e2r3g4e5f6
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't1h2e3o4r5y6'
down_revision: Union[str, Sequence[str], None] = 'm1e2r3g4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The classification subjects at the time of this migration: three lessons each.
# Badge theory is one. Copied rather than imported so a later catalogue change
# can't change what this migration did.
THREE_LESSON_SUBJECTS = (
    'acp_32_2', 'acp_33_2', 'acp_34_2', 'acp_32_3', 'air_power', 'acp_34_3', 'acp_33_4',
    'jet_engine_propulsion', 'military_aircraft', 'acp_33_3', 'acp_32_4', 'acp_35_3',
    'rocketry', 'acp_35_4',
)


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if 'Cadet_Theory_Lessons' not in tables:
        op.create_table(
            'Cadet_Theory_Lessons',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('lesson_key', sa.Text(), nullable=False),
            sa.Column('part', sa.Integer(), nullable=False),
            sa.Column('attended_at', sa.DateTime(), nullable=False),
            sa.Column('recorded_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('cadet_id', 'lesson_key', 'part', name='uq_theory_lesson_cadet_part'),
        )

        # A subject staff marked before lessons were counted was taught in full:
        # give it every lesson, so it still reads as taught and clearing one
        # lesson later leaves the others.
        three = ", ".join(f"'{key}'" for key in THREE_LESSON_SUBJECTS)
        for part in (1, 2, 3):
            only_three = "" if part == 1 else f" AND lesson_key IN ({three})"
            op.execute(
                'INSERT INTO "Cadet_Theory_Lessons" (cadet_id, lesson_key, part, attended_at, recorded_by) '
                f'SELECT cadet_id, lesson_key, {part}, completed_at, recorded_by FROM "Cadet_Theory_Progress" '
                "WHERE (recorded_by IS NULL OR recorded_by <> 'Volunteer Portal')" + only_three
            )

    if 'Theory_Lesson_Codes' not in tables:
        op.create_table(
            'Theory_Lesson_Codes',
            sa.Column('code', sa.Text(), nullable=False),
            sa.Column('lesson_key', sa.Text(), nullable=False),
            sa.Column('part', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('code'),
            sa.UniqueConstraint('lesson_key', 'part', name='uq_theory_code_lesson_part'),
        )


def downgrade() -> None:
    op.drop_table('Theory_Lesson_Codes')
    op.drop_table('Cadet_Theory_Lessons')
