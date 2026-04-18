"""add badge grid tables

Revision ID: a9b0c1d2e3f4
Revises: 37800a9c494f
Create Date: 2026-04-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9b0c1d2e3f4'
down_revision: Union[str, Sequence[str], None] = '37800a9c494f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = sa.inspect(conn).get_table_names()

    if 'Badge_Grid_Config' not in existing:
        op.create_table(
            'Badge_Grid_Config',
            sa.Column('id',       sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('num_rows', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('num_cols', sa.Integer(), nullable=False, server_default='1'),
        )

    if 'Badge_Grid_Cells' not in existing:
        op.create_table(
            'Badge_Grid_Cells',
            sa.Column('id',    sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('row',   sa.Integer(), nullable=False),
            sa.Column('col',   sa.Integer(), nullable=False),
            sa.Column('label', sa.Text(),    nullable=True),
        )

    if 'Badge_Items' not in existing:
        op.create_table(
            'Badge_Items',
            sa.Column('id',      sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('cell_id', sa.Integer(), sa.ForeignKey('Badge_Grid_Cells.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name',    sa.Text(),    nullable=False),
        )


def downgrade() -> None:
    op.drop_table('Badge_Items')
    op.drop_table('Badge_Grid_Cells')
    op.drop_table('Badge_Grid_Config')
