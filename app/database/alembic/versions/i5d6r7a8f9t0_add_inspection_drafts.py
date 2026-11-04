"""inspection drafts saved on the server while a sheet is being marked

Revision ID: i5d6r7a8f9t0
Revises: p1r2o3t4e5c6
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'i5d6r7a8f9t0'
down_revision: Union[str, Sequence[str], None] = 'p1r2o3t4e5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Inspection_Drafts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('owner', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner'),
    )


def downgrade() -> None:
    op.drop_table('Inspection_Drafts')
