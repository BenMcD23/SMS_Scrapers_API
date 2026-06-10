"""add ready_to_collect to order items

Revision ID: g1h2i3j4k5l6
Revises: a3f1c9d2b4e5
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g1h2i3j4k5l6'
down_revision: Union[str, Sequence[str], None] = 'a3f1c9d2b4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("Stores_Order_Items", sa.Column("ready_to_collect", sa.DateTime(), nullable=True))
    op.add_column("Badge_Order_Items",  sa.Column("ready_to_collect", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("Stores_Order_Items", "ready_to_collect")
    op.drop_column("Badge_Order_Items",  "ready_to_collect")
