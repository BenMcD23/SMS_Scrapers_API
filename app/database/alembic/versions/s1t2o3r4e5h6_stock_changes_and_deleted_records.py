"""add Stores_Stock_Changes and Stores_Deleted_Records

A log of every stock count change (uniform shelves and the badge grid), and a
copy of each stores order, Logs Form entry and badge order-list entry taken as
it is deleted, so it can be seen and restored.

Revision ID: s1t2o3r4e5h6
Revises: i1s2h3i4s5t6
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's1t2o3r4e5h6'
down_revision: Union[str, Sequence[str], None] = 'i1s2h3i4s5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Stores_Stock_Changes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('stock_item_id', sa.Integer(), nullable=True),
        sa.Column('item_name', sa.Text(), nullable=False),
        sa.Column('size', sa.Text(), nullable=True),
        sa.Column('location', sa.Text(), nullable=True),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('change', sa.Integer(), nullable=False),
        sa.Column('quantity_after', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.Column('changed_by', sa.Text(), nullable=True),
    )
    op.create_index('ix_Stores_Stock_Changes_stock_item_id', 'Stores_Stock_Changes', ['stock_item_id'])
    op.create_index('ix_Stores_Stock_Changes_changed_at', 'Stores_Stock_Changes', ['changed_at'])
    op.create_table(
        'Stores_Deleted_Records',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_by', sa.Text(), nullable=True),
        sa.Column('restored_at', sa.DateTime(), nullable=True),
        sa.Column('restored_by', sa.Text(), nullable=True),
        sa.Column('restored_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_Stores_Deleted_Records_kind', 'Stores_Deleted_Records', ['kind'])
    op.create_index('ix_Stores_Deleted_Records_deleted_at', 'Stores_Deleted_Records', ['deleted_at'])


def downgrade() -> None:
    op.drop_table('Stores_Deleted_Records')
    op.drop_table('Stores_Stock_Changes')
