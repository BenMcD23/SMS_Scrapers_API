"""add badge orders

Revision ID: e1f2a3b4c5d6
Revises: 3ab3d0812d40
Branch Labels: None
Depends On: None

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = '3ab3d0812d40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = sa.inspect(conn).get_table_names()

    if 'Badge_Orders' not in existing:
        op.create_table(
            'Badge_Orders',
            sa.Column('id',         sa.Integer(),    nullable=False, autoincrement=True),
            sa.Column('cadet_id',   sa.BigInteger(), nullable=False),
            sa.Column('created_at', sa.DateTime(),   nullable=False),
            sa.Column('completed',  sa.Boolean(),    nullable=False, server_default='0'),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin']),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Badge_Order_Items' not in existing:
        op.create_table(
            'Badge_Order_Items',
            sa.Column('id',         sa.Integer(), nullable=False, autoincrement=True),
            sa.Column('order_id',   sa.Integer(), nullable=False),
            sa.Column('badge_name', sa.Text(),    nullable=False),
            sa.Column('qm_notes',   sa.Text(),    nullable=False, server_default='[]'),
            sa.Column('given_at',   sa.DateTime(), nullable=True),
            sa.Column('given_by',   sa.Text(),     nullable=True),
            sa.ForeignKeyConstraint(['order_id'], ['Badge_Orders.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Badge_Order_Items')
    op.drop_table('Badge_Orders')
