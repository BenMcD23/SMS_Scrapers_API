"""add completed to stores orders

Revision ID: f2a3b4c5d6e7
Revises: e6f7a8b9c0d1
Branch Labels: None
Depends On: None

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Stores_Orders', sa.Column('completed', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('Stores_Orders', 'completed')
