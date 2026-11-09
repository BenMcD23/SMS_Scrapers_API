"""add training nights and programme lesson links

Programme_Lesson_Links says which theory-catalogue lesson a programme activity
is; Training_Night_Sessions holds the per-night sessions and their grouping
inputs (routers/training.py).

Revision ID: t7r8a9i0n1g2
Revises: r6p7o8l9o0s1
Create Date: 2026-11-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 't7r8a9i0n1g2'
down_revision: Union[str, Sequence[str], None] = 'r6p7o8l9o0s1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Programme_Lesson_Links',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('activity', sa.Text(), nullable=False, unique=True),
        sa.Column('subject_key', sa.Text(), nullable=True),
        sa.Column('new_subject', sa.Text(), nullable=True),
        sa.Column('part', sa.Integer(), nullable=True),
        sa.Column('confirmed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=True),
    )
    op.create_table(
        'Training_Night_Sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('period', sa.Integer(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('subject_key', sa.Text(), nullable=True),
        sa.Column('part', sa.Integer(), nullable=True),
        sa.Column('flights', sa.JSON(), nullable=False),
        sa.Column('pool', sa.Text(), nullable=False),
        sa.Column('include', sa.Text(), nullable=False),
        sa.Column('grouping', sa.Text(), nullable=False),
        sa.Column('group_count', sa.Integer(), nullable=True),
        sa.Column('max_group', sa.Integer(), nullable=True),
        sa.Column('instructor', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_Training_Night_Sessions_date', 'Training_Night_Sessions', ['date'])


def downgrade() -> None:
    op.drop_index('ix_Training_Night_Sessions_date', table_name='Training_Night_Sessions')
    op.drop_table('Training_Night_Sessions')
    op.drop_table('Programme_Lesson_Links')
