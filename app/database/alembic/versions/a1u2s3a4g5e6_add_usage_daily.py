"""add usage daily

Revision ID: a1u2s3a4g5e6
Revises: e8f9a0b1c2d3
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1u2s3a4g5e6'
down_revision: Union[str, Sequence[str], None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if 'Usage_Daily' not in tables:
        op.create_table(
            'Usage_Daily',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('day', sa.Date(), nullable=False),
            sa.Column('method', sa.Text(), nullable=False),
            sa.Column('route', sa.Text(), nullable=False),
            sa.Column('email', sa.Text(), nullable=False, server_default=''),
            sa.Column('calls', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('day', 'method', 'route', 'email', name='uq_usage_daily'),
        )


def downgrade() -> None:
    op.drop_table('Usage_Daily')
