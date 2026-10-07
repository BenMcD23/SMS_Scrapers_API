"""add cadet snapshots and stats targets

Revision ID: c5s6n7a8p9t0
Revises: a1u2s3a4g5e6
Create Date: 2026-10-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5s6n7a8p9t0'
down_revision: Union[str, Sequence[str], None] = 'a1u2s3a4g5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if 'Cadet_Snapshots' not in tables:
        op.create_table(
            'Cadet_Snapshots',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('snapshot_id', sa.Integer(), nullable=False),
            sa.Column('cin', sa.BigInteger(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('flight', sa.Text(), nullable=True),
            sa.Column('rank', sa.Text(), nullable=True),
            sa.Column('classification', sa.Text(), nullable=True),
            sa.Column('junior', sa.Boolean(), nullable=False),
            sa.Column('badges', sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(['snapshot_id'], ['Stats_Snapshots.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_Cadet_Snapshots_snapshot_id', 'Cadet_Snapshots', ['snapshot_id'])
        op.create_index('ix_Cadet_Snapshots_cin', 'Cadet_Snapshots', ['cin'])

    if 'Stats_Targets' not in tables:
        op.create_table(
            'Stats_Targets',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('badge', sa.Text(), nullable=False),
            sa.Column('min_level', sa.Text(), nullable=True),
            sa.Column('flight', sa.Text(), nullable=True),
            sa.Column('exclude_juniors', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('target_pct', sa.Integer(), nullable=False),
            sa.Column('due', sa.Date(), nullable=False),
            sa.Column('created_by', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Stats_Targets')
    op.drop_index('ix_Cadet_Snapshots_cin', table_name='Cadet_Snapshots')
    op.drop_index('ix_Cadet_Snapshots_snapshot_id', table_name='Cadet_Snapshots')
    op.drop_table('Cadet_Snapshots')
