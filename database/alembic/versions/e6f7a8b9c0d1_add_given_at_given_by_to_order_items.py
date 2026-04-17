"""add given_at and given_by to order items

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Stores_Order_Items', sa.Column('given_at', sa.DateTime(), nullable=True))
    op.add_column('Stores_Order_Items', sa.Column('given_by', sa.Text(),     nullable=True))


def downgrade() -> None:
    op.drop_column('Stores_Order_Items', 'given_by')
    op.drop_column('Stores_Order_Items', 'given_at')
