"""add replacement to badge order items

Revision ID: p2q3r4s5t6u7
Revises: o1p2q3r4s5t6
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p2q3r4s5t6u7'
down_revision: Union[str, Sequence[str], None] = 'o1p2q3r4s5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Badge_Order_Items'"
    ))}
    if 'replacement' not in cols:
        op.add_column('Badge_Order_Items', sa.Column('replacement', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('Badge_Order_Items', 'replacement')
