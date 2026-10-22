"""activity logs (DofE sections)

Staff switch a cadet's activity log on; the cadet logs what they did each week
for Skills, Volunteering and Physical.

Revision ID: a1c2t3l4o5g6
Revises: m1s2s3n4g5c6
Create Date: 2026-10-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c2t3l4o5g6'
down_revision: Union[str, Sequence[str], None] = 'm1s2s3n4g5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Activity_Log_Enrolments',
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('level', sa.Text(), nullable=False),
        sa.Column('enabled_at', sa.DateTime(), nullable=False),
        sa.Column('enabled_by', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('cadet_id'),
    )
    op.create_table(
        'Activity_Logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('section', sa.Text(), nullable=False),
        sa.Column('level', sa.Text(), nullable=False),
        sa.Column('activity', sa.Text(), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False),
        sa.Column('assessor_name', sa.Text(), nullable=False),
        sa.Column('assessor_role', sa.Text(), nullable=False),
        sa.Column('duration_months', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cadet_id', 'section', 'level', name='uq_activity_log_section'),
    )
    op.create_index('ix_Activity_Logs_cadet_id', 'Activity_Logs', ['cadet_id'])
    op.create_table(
        'Activity_Log_Entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('log_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('minutes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['log_id'], ['Activity_Logs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_Activity_Log_Entries_log_id', 'Activity_Log_Entries', ['log_id'])


def downgrade() -> None:
    op.drop_index('ix_Activity_Log_Entries_log_id', table_name='Activity_Log_Entries')
    op.drop_table('Activity_Log_Entries')
    op.drop_index('ix_Activity_Logs_cadet_id', table_name='Activity_Logs')
    op.drop_table('Activity_Logs')
    op.drop_table('Activity_Log_Enrolments')
