"""add cadet portal data (Volunteer Portal sync)

Revision ID: v1p2s3y4n5c6
Revises: c5s6n7a8p9t0
Create Date: 2026-10-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'v1p2s3y4n5c6'
down_revision: Union[str, Sequence[str], None] = 'c5s6n7a8p9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if 'Cadet_Portal_Data' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'Cadet_Portal_Data',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('dataset', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cadet_id', 'dataset'),
    )
    op.create_index('ix_Cadet_Portal_Data_cadet_id', 'Cadet_Portal_Data', ['cadet_id'])


def downgrade() -> None:
    op.drop_index('ix_Cadet_Portal_Data_cadet_id', table_name='Cadet_Portal_Data')
    op.drop_table('Cadet_Portal_Data')
