"""add Users.nav_pins

The pages each person pinned to the SMS site's menu and phone bar, kept on the
account so they follow them between browsers and devices.

Revision ID: n1a2v3p4i5n6
Revises: s7n8o9t0e1s2
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'n1a2v3p4i5n6'
down_revision: Union[str, Sequence[str], None] = 's7n8o9t0e1s2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable with no default: NULL is "never chosen", which the site turns
    # into the role's default pins.
    op.add_column('Users', sa.Column('nav_pins', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('Users', 'nav_pins')
