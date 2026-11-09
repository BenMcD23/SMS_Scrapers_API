"""add recruit polo size

The polo size asked on the recruitment-evening sign-up page.

Revision ID: r6p7o8l9o0s1
Revises: c1h2e3c4k5i6
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r6p7o8l9o0s1'
down_revision: Union[str, Sequence[str], None] = 'c1h2e3c4k5i6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Recruit_Applications', sa.Column('polo_size', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('Recruit_Applications', 'polo_size')
