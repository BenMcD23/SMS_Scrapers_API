"""add cadet theory progress table

Tracks when a cadet has completed the theory element of a lesson but not yet
the formal assessment/qualification, so part-finished progress is visible.

Revision ID: l1m2n3o4p5q6
Revises: k1l2m3n4o5p6
Create Date: 2026-07-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l1m2n3o4p5q6'
down_revision: Union[str, Sequence[str], None] = 'k1l2m3n4o5p6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}
    if 'Cadet_Theory_Progress' not in tables:
        op.create_table(
            'Cadet_Theory_Progress',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('lesson_key', sa.Text(), nullable=False),
            sa.Column('completed_at', sa.DateTime(), nullable=False),
            sa.Column('recorded_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('cadet_id', 'lesson_key', name='uq_theory_cadet_lesson'),
        )


def downgrade() -> None:
    op.drop_table('Cadet_Theory_Progress')
