"""cadet check-ins before the register, and the squadron's check-in code

Also merges the two heads main was left with (the badge order list history and
the custom theory subjects), so ``alembic upgrade head`` has one head again.

Revision ID: c1h2e3c4k5i6
Revises: b1l2i3s4t5h6, q2r3c4s5u6b7
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1h2e3c4k5i6'
down_revision: Union[str, Sequence[str], None] = ('b1l2i3s4t5h6', 'q2r3c4s5u6b7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Cadet_Check_Ins',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('checked_in_at', sa.DateTime(), nullable=False),
        sa.Column('checked_in_by', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cadet_id', 'date', name='uq_cadet_check_in_night'),
    )
    op.create_index('ix_Cadet_Check_Ins_cadet_id', 'Cadet_Check_Ins', ['cadet_id'])
    op.create_index('ix_Cadet_Check_Ins_date', 'Cadet_Check_Ins', ['date'])
    op.create_table(
        'Check_In_Settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.Column('changed_by', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('Check_In_Settings')
    op.drop_index('ix_Cadet_Check_Ins_date', table_name='Cadet_Check_Ins')
    op.drop_index('ix_Cadet_Check_Ins_cadet_id', table_name='Cadet_Check_Ins')
    op.drop_table('Cadet_Check_Ins')
