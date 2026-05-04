"""add stats snapshots table

Revision ID: a1b2c3d4e5f6
Revises: 54b84df7e8ce
Create Date: 2026-03-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '54b84df7e8ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}
    if 'Stats_Snapshots' not in tables:
        op.create_table(
            'Stats_Snapshots',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('captured_at', sa.DateTime(), nullable=False),
            sa.Column('data', sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Stats_Snapshots')
