"""session planner: the task bank and the kit list

Revision ID: s1e2s3k4i5t6
Revises: r5s6i7g8n9u0
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's1e2s3k4i5t6'
down_revision: Union[str, Sequence[str], None] = 'r5s6i7g8n9u0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Session_Kit',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('notes', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'Session_Tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('number', sa.Text(), nullable=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('brief', sa.Text(), server_default='', nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('min_group', sa.Integer(), nullable=True),
        sa.Column('max_group', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_Session_Tasks_kind'), 'Session_Tasks', ['kind'], unique=False)
    op.create_table(
        'Session_Task_Kit',
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('kit_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['Session_Tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['kit_id'], ['Session_Kit.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('task_id', 'kit_id'),
    )
    op.create_index(op.f('ix_Session_Task_Kit_kit_id'), 'Session_Task_Kit', ['kit_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_Session_Task_Kit_kit_id'), table_name='Session_Task_Kit')
    op.drop_table('Session_Task_Kit')
    op.drop_index(op.f('ix_Session_Tasks_kind'), table_name='Session_Tasks')
    op.drop_table('Session_Tasks')
    op.drop_table('Session_Kit')
