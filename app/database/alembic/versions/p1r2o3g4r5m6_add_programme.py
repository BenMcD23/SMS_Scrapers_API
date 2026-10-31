"""add programme nights and sessions

Revision ID: p1r2o3g4r5m6
Revises: a7s8i9s0t1c2
Create Date: 2026-10-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p1r2o3g4r5m6'
down_revision: Union[str, Sequence[str], None] = 'a7s8i9s0t1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Programme_Nights',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('uniform', sa.Text(), nullable=True),
        sa.Column('duty_nco', sa.Text(), nullable=True),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date'),
    )
    op.create_table(
        'Programme_Sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('night_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.Integer(), nullable=False),
        sa.Column('flight', sa.Text(), nullable=False),
        sa.Column('activity', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('instructor', sa.Text(), nullable=True),
        sa.Column('split', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('raw', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['night_id'], ['Programme_Nights.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_Programme_Sessions_night_id', 'Programme_Sessions', ['night_id'])


def downgrade() -> None:
    op.drop_index('ix_Programme_Sessions_night_id', table_name='Programme_Sessions')
    op.drop_table('Programme_Sessions')
    op.drop_table('Programme_Nights')
