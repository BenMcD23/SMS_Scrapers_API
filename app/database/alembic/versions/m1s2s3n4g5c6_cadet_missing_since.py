"""Cadets.missing_since and Removed_Cadets

The quali scraper flags cadets it no longer finds on SMS instead of deleting
them; staff remove them, and removed CINs aren't scraped back in.

Revision ID: m1s2s3n4g5c6
Revises: r3c4u5i6t7s8
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm1s2s3n4g5c6'
down_revision: Union[str, Sequence[str], None] = 'r3c4u5i6t7s8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Cadets', sa.Column('missing_since', sa.DateTime(), nullable=True))
    op.create_table(
        'Removed_Cadets',
        sa.Column('cin', sa.BigInteger(), primary_key=True),
        sa.Column('first_name', sa.Text(), nullable=True),
        sa.Column('last_name', sa.Text(), nullable=True),
        sa.Column('removed_at', sa.DateTime(), nullable=False),
        sa.Column('removed_by', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('Removed_Cadets')
    op.drop_column('Cadets', 'missing_since')
