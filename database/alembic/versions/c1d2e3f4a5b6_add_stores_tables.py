"""add stores tables

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f6
Create Date: 2026-03-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}

    if 'Stores_Boxes' not in tables:
        op.create_table(
            'Stores_Boxes',
            sa.Column('id',    sa.Integer(), nullable=False),
            sa.Column('label', sa.Text(),    nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('label'),
        )

    if 'Stores_Sections' not in tables:
        op.create_table(
            'Stores_Sections',
            sa.Column('id',     sa.Integer(), nullable=False),
            sa.Column('box_id', sa.Integer(), nullable=False),
            sa.Column('label',  sa.Text(),    nullable=False),
            sa.ForeignKeyConstraint(['box_id'], ['Stores_Boxes.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Stores_Items' not in tables:
        op.create_table(
            'Stores_Items',
            sa.Column('id',         sa.Integer(), nullable=False),
            sa.Column('item_type',  sa.Text(),    nullable=False),
            sa.Column('size',       sa.Text(),    nullable=False),
            sa.Column('quantity',   sa.Integer(), nullable=False),
            sa.Column('gender',     sa.Text(),    nullable=False),
            sa.Column('box_id',     sa.Integer(), nullable=False),
            sa.Column('section_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['box_id'],     ['Stores_Boxes.id'],    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['section_id'], ['Stores_Sections.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Stores_Orders' not in tables:
        op.create_table(
            'Stores_Orders',
            sa.Column('id',         sa.Integer(),  nullable=False),
            sa.Column('cadet_id',   sa.Integer(),  nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin']),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Stores_Order_Items' not in tables:
        op.create_table(
            'Stores_Order_Items',
            sa.Column('id',          sa.Integer(), nullable=False),
            sa.Column('order_id',    sa.Integer(), nullable=False),
            sa.Column('item_type',   sa.Text(),    nullable=False),
            sa.Column('size',        sa.Text(),    nullable=False, server_default=''),
            sa.Column('need_sizing', sa.Boolean(), nullable=False, server_default='0'),
            sa.ForeignKeyConstraint(['order_id'], ['Stores_Orders.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Stores_Order_Items')
    op.drop_table('Stores_Orders')
    op.drop_table('Stores_Items')
    op.drop_table('Stores_Sections')
    op.drop_table('Stores_Boxes')
