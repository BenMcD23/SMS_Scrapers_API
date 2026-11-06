"""add editable uniform and badge catalogue

Revision ID: c1a2t3a4l5o6
Revises: r5s6i7g8n9u0
Create Date: 2026-11-05 12:00:00.000000

The rows themselves are seeded from core/catalogue.py the first time the
catalogue is read, so this only creates the tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2t3a4l5o6'
down_revision: Union[str, Sequence[str], None] = 'r5s6i7g8n9u0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if 'Catalogue_Item_Types' not in tables:
        op.create_table(
            'Catalogue_Item_Types',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('gender', sa.Text(), nullable=False, server_default='unisex'),
            sa.Column('issuance_category', sa.Text(), nullable=True),
            sa.Column('no_size', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('stock_only', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('kit_flight', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('on_order_list', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('low_stock_level', sa.Integer(), nullable=True),
            sa.Column('sizes', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('sizing_fields', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    if 'Catalogue_Badge_Categories' not in tables:
        op.create_table(
            'Catalogue_Badge_Categories',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('slug', sa.Text(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('prefix', sa.Text(), nullable=True),
            sa.Column('levels', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('items', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('gained_where', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('slug'),
        )


def downgrade() -> None:
    op.drop_table('Catalogue_Badge_Categories')
    op.drop_table('Catalogue_Item_Types')
