"""assistant chats can be protected from the 30-day cleanup

Revision ID: p1r2o3t4e5c6
Revises: d1o2f3e4a5s6
Create Date: 2026-11-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p1r2o3t4e5c6'
down_revision: Union[str, Sequence[str], None] = 'd1o2f3e4a5s6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Assistant_Chats', sa.Column('protected', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('Assistant_Chats', 'protected')
