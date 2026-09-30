"""add VP sync tables

Revision ID: 7a5c0e19d3b4
Revises: e8f9a0b1c2d3
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7a5c0e19d3b4'
down_revision: Union[str, Sequence[str], None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = sa.inspect(conn).get_table_names()

    if 'VP_People' not in existing:
        op.create_table(
            'VP_People',
            sa.Column('personnel_web_id', sa.Text(),       primary_key=True),
            sa.Column('cin',              sa.BigInteger(), nullable=False),
            sa.Column('person_type',      sa.Text(),       nullable=False),
            sa.Column('profile',          sa.JSON(),       nullable=False),
            sa.Column('synced_at',        sa.DateTime(),   nullable=False),
            sa.Column('synced_by',        sa.Text(),       nullable=False),
        )
        op.create_index('ix_VP_People_cin', 'VP_People', ['cin'])

    if 'VP_Records' not in existing:
        op.create_table(
            'VP_Records',
            sa.Column('id',               sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('personnel_web_id', sa.Text(),
                      sa.ForeignKey('VP_People.personnel_web_id', ondelete='CASCADE'), nullable=False),
            sa.Column('dataset',          sa.Text(),     nullable=False),
            sa.Column('payload',          sa.JSON(),     nullable=True),
            sa.Column('synced_at',        sa.DateTime(), nullable=False),
            sa.Column('synced_by',        sa.Text(),     nullable=False),
            sa.UniqueConstraint('personnel_web_id', 'dataset', name='uq_vp_record_person_dataset'),
        )


def downgrade() -> None:
    op.drop_table('VP_Records')
    op.drop_index('ix_VP_People_cin', table_name='VP_People')
    op.drop_table('VP_People')
