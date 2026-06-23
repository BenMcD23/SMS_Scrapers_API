"""add staff table

Revision ID: h3i4j5k6l7m8
Revises: g3h4i5j6k7l8
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h3i4j5k6l7m8'
down_revision: Union[str, Sequence[str], None] = 'g3h4i5j6k7l8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='Staff'"
    ))}
    if 'Staff' not in tables:
        op.create_table(
            'Staff',
            sa.Column('cin', sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column('first_name', sa.Text(), nullable=False),
            sa.Column('last_name', sa.Text(), nullable=False),
            sa.Column('rank', sa.Text(), nullable=True),
            sa.Column('email', sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table('Staff')
