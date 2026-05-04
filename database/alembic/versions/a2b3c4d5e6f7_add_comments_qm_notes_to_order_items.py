"""add comments, sizing_details, and qm_notes to order items

Revision ID: a2b3c4d5e6f7
Revises: f7g8h9i0j1k2
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f7g8h9i0j1k2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Stores_Order_Items'"
    ))}
    if 'comments' not in cols:
        op.add_column('Stores_Order_Items',
            sa.Column('comments', sa.Text(), nullable=False, server_default=''))
    if 'sizing_details' not in cols:
        op.add_column('Stores_Order_Items',
            sa.Column('sizing_details', sa.Text(), nullable=False, server_default=''))
    if 'qm_notes' not in cols:
        op.add_column('Stores_Order_Items',
            sa.Column('qm_notes', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('Stores_Order_Items', 'qm_notes')
    op.drop_column('Stores_Order_Items', 'sizing_details')
    op.drop_column('Stores_Order_Items', 'comments')
