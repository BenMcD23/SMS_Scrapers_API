"""add committee request withdrawal fields

Revision ID: q1r2s3t4u5v6
Revises: p1q2r3s4t5u6
Create Date: 2026-07-27 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'q1r2s3t4u5v6'
down_revision: Union[str, Sequence[str], None] = 'p1q2r3s4t5u6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if 'Committee_Requests' not in tables:
        return
    cols = {c['name'] for c in inspector.get_columns('Committee_Requests')}
    if 'withdrawn_at' not in cols:
        op.add_column('Committee_Requests', sa.Column('withdrawn_at', sa.DateTime(), nullable=True))
    if 'withdrawn_by' not in cols:
        op.add_column('Committee_Requests', sa.Column('withdrawn_by', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('Committee_Requests', 'withdrawn_by')
    op.drop_column('Committee_Requests', 'withdrawn_at')
