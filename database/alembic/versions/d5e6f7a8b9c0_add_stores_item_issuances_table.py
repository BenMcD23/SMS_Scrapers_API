"""add stores item issuances table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Stores_Item_Issuances',
        sa.Column('id',            sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('cadet_id',      sa.BigInteger(), sa.ForeignKey('Cadets.cin', ondelete='CASCADE'), nullable=False),
        sa.Column('item_category', sa.Text(),       nullable=False),
        sa.Column('last_given',    sa.DateTime(),   nullable=False),
        sa.Column('size_given',    sa.Text(),       nullable=True),
    )


def downgrade() -> None:
    op.drop_table('Stores_Item_Issuances')
