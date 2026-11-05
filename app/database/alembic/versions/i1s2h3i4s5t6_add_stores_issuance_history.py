"""add Stores_Issuance_History

Every uniform give is kept, so marking an item as given no longer overwrites
the record before it, and an undo steps back to that record. Each existing
"last issued" record is copied in as its first entry, so nothing on file today
is lost and it can be undone like any other.

Revision ID: i1s2h3i4s5t6
Revises: a5d6r7a8f9t0
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i1s2h3i4s5t6'
down_revision: Union[str, Sequence[str], None] = 'a5d6r7a8f9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Stores_Issuance_History',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cadet_id', sa.BigInteger(), sa.ForeignKey('Cadets.cin', ondelete='CASCADE'), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('Users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('item_category', sa.Text(), nullable=False),
        sa.Column('given_at', sa.DateTime(), nullable=False),
        sa.Column('size_given', sa.Text(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('recorded_by', sa.Text(), nullable=True),
        sa.Column('order_item_id', sa.Integer(),
                  sa.ForeignKey('Stores_Order_Items.id', ondelete='SET NULL'), nullable=True),
        sa.Column('undone_at', sa.DateTime(), nullable=True),
        sa.Column('undone_by', sa.Text(), nullable=True),
    )
    for column in ('cadet_id', 'user_id', 'order_item_id'):
        op.create_index(f'ix_Stores_Issuance_History_{column}', 'Stores_Issuance_History', [column])
    # Who recorded these is unknown, so recorded_by stays null.
    op.execute(
        'INSERT INTO "Stores_Issuance_History" '
        '(cadet_id, user_id, item_category, given_at, size_given, recorded_at) '
        'SELECT cadet_id, user_id, item_category, last_given, size_given, last_given '
        'FROM "Stores_Item_Issuances" ORDER BY last_given, id'
    )


def downgrade() -> None:
    op.drop_table('Stores_Issuance_History')
