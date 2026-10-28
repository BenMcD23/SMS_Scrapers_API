"""add cadet weapon handling tests

Revision ID: w1h2t3s4c5d6
Revises: d1b2i3n4d5x6
Create Date: 2026-10-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'w1h2t3s4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd1b2i3n4d5x6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Cadet_WHTs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('weapon', sa.Text(), nullable=False),
        sa.Column('date_passed', sa.Date(), nullable=False),
        sa.Column('date_expires', sa.Date(), nullable=True),
        sa.Column('awarded_by', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_Cadet_WHTs_cadet_id', 'Cadet_WHTs', ['cadet_id'])


def downgrade() -> None:
    op.drop_index('ix_Cadet_WHTs_cadet_id', table_name='Cadet_WHTs')
    op.drop_table('Cadet_WHTs')
