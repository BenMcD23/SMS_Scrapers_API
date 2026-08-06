"""add nco holidays

Revision ID: u1v2w3x4y5z6
Revises: t1u2v3w4x5y6
Create Date: 2026-08-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'u1v2w3x4y5z6'
down_revision: Union[str, Sequence[str], None] = 't1u2v3w4x5y6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if 'NCO_Holidays' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'NCO_Holidays',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date_from', sa.DateTime(), nullable=False),
        sa.Column('date_to', sa.DateTime(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('booked_by_name', sa.Text(), nullable=False),
        sa.Column('booked_by_email', sa.Text(), nullable=False),
        sa.Column('google_event_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_by_name', sa.Text(), nullable=True),
        sa.Column('cancelled_by_email', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['Users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_nco_holidays_user_id', 'NCO_Holidays', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_nco_holidays_user_id', table_name='NCO_Holidays')
    op.drop_table('NCO_Holidays')
