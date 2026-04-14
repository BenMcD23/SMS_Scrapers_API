"""add comments, sizing_details, and qm_notes to order items

Revision ID: a2b3c4d5e6f7
Revises: f7g8h9i0j1k2
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f7g8h9i0j1k2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Stores_Order_Items',
        sa.Column('comments', sa.Text(), nullable=False, server_default=''))
    op.add_column('Stores_Order_Items',
        sa.Column('sizing_details', sa.Text(), nullable=False, server_default=''))
    # qm_notes stores a JSON array of {id, content, timestamp, addedBy} objects
    op.add_column('Stores_Order_Items',
        sa.Column('qm_notes', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('Stores_Order_Items', 'qm_notes')
    op.drop_column('Stores_Order_Items', 'sizing_details')
    op.drop_column('Stores_Order_Items', 'comments')
