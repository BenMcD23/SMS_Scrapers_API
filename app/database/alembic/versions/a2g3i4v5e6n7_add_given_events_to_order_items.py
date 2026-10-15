"""add given_events to order items

Append-only log of "given" and "undone" on uniform and badge order items, so
undoing a mark-as-given keeps who gave it, who undid it, and when.

Revision ID: a2g3i4v5e6n7
Revises: v1s2k3i4r5t6
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2g3i4v5e6n7'
down_revision: Union[str, Sequence[str], None] = 'v1s2k3i4r5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table: str) -> set:
    return {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
    ), {"t": table})}


def upgrade() -> None:
    conn = op.get_bind()
    for table in ('Stores_Order_Items', 'Badge_Order_Items'):
        if 'given_events' not in _columns(conn, table):
            op.add_column(table, sa.Column(
                'given_events', sa.Text(), nullable=False, server_default='[]'
            ))


def downgrade() -> None:
    op.drop_column('Badge_Order_Items', 'given_events')
    op.drop_column('Stores_Order_Items', 'given_events')
