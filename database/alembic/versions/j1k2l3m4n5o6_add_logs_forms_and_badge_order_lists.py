"""add logs forms and badge order lists

Revision ID: j1k2l3m4n5o6
Revises: h5j6k7l8m9n0
Branch Labels: None
Depends On: None

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j1k2l3m4n5o6'
down_revision: Union[str, Sequence[str], None] = 'h5j6k7l8m9n0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = sa.inspect(conn).get_table_names()

    if 'Logs_Forms' not in existing:
        op.create_table(
            'Logs_Forms',
            sa.Column('id',         sa.Integer(),  nullable=False, autoincrement=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('ordered_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Logs_Form_Entries' not in existing:
        op.create_table(
            'Logs_Form_Entries',
            sa.Column('id',            sa.Integer(),    nullable=False, autoincrement=True),
            sa.Column('form_id',       sa.Integer(),    nullable=False),
            sa.Column('order_item_id', sa.Integer(),    nullable=True),
            sa.Column('item_type',     sa.Text(),       nullable=False),
            sa.Column('size',          sa.Text(),       nullable=False, server_default=''),
            sa.Column('cadet_name',    sa.Text(),       nullable=False),
            sa.Column('cadet_cin',     sa.BigInteger(), nullable=True),
            sa.Column('created_at',    sa.DateTime(),   nullable=False),
            sa.ForeignKeyConstraint(['form_id'], ['Logs_Forms.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['order_item_id'], ['Stores_Order_Items.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('order_item_id'),
        )

    if 'Badge_Order_Lists' not in existing:
        op.create_table(
            'Badge_Order_Lists',
            sa.Column('id',         sa.Integer(),  nullable=False, autoincrement=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('ordered_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Badge_Order_List_Entries' not in existing:
        op.create_table(
            'Badge_Order_List_Entries',
            sa.Column('id',            sa.Integer(),  nullable=False, autoincrement=True),
            sa.Column('list_id',       sa.Integer(),  nullable=False),
            sa.Column('order_item_id', sa.Integer(),  nullable=True),
            sa.Column('badge_name',    sa.Text(),     nullable=False),
            sa.Column('cadet_name',    sa.Text(),     nullable=False),
            sa.Column('created_at',    sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['list_id'], ['Badge_Order_Lists.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['order_item_id'], ['Badge_Order_Items.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('order_item_id'),
        )


def downgrade() -> None:
    op.drop_table('Badge_Order_List_Entries')
    op.drop_table('Badge_Order_Lists')
    op.drop_table('Logs_Form_Entries')
    op.drop_table('Logs_Forms')
