"""add history to Badge_Order_List_Entries

Each step on the badge order list (added, ordered, received) can now be
undone, which clears its stamp; this log keeps every step and undo.

Revision ID: b1l2i3s4t5h6
Revises: s1t2o3r4e5h6
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1l2i3s4t5h6'
down_revision: Union[str, Sequence[str], None] = 's1t2o3r4e5h6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Badge_Order_List_Entries',
                  sa.Column('history', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('Badge_Order_List_Entries', 'history')
