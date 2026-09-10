"""add gained where to badge order items

Revision ID: a5b6c7d8e9f0
Revises: b5c6d7e8f9a0
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Badge_Order_Items'"
    ))}
    if 'gained_where' not in cols:
        op.add_column('Badge_Order_Items', sa.Column('gained_where', sa.Text(), nullable=True))
    if 'gained_where_detail' not in cols:
        op.add_column('Badge_Order_Items', sa.Column('gained_where_detail', sa.Text(), nullable=True))
    if 'gained_date_from' not in cols:
        op.add_column('Badge_Order_Items', sa.Column('gained_date_from', sa.DateTime(), nullable=True))
    if 'gained_date_to' not in cols:
        op.add_column('Badge_Order_Items', sa.Column('gained_date_to', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('Badge_Order_Items', 'gained_date_to')
    op.drop_column('Badge_Order_Items', 'gained_date_from')
    op.drop_column('Badge_Order_Items', 'gained_where_detail')
    op.drop_column('Badge_Order_Items', 'gained_where')
