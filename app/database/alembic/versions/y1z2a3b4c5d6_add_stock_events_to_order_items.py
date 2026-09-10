"""add stock_events to order items

Revision ID: y1z2a3b4c5d6
Revises: x1y2z3a4b5c6
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'y1z2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'x1y2z3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table: str) -> set:
    return {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
    ), {"t": table})}


def upgrade() -> None:
    conn = op.get_bind()
    for table in ('Stores_Order_Items', 'Badge_Order_Items'):
        if 'stock_events' not in _columns(conn, table):
            op.add_column(table, sa.Column(
                'stock_events', sa.Text(), nullable=False, server_default='[]'
            ))


def downgrade() -> None:
    op.drop_column('Badge_Order_Items', 'stock_events')
    op.drop_column('Stores_Order_Items', 'stock_events')
