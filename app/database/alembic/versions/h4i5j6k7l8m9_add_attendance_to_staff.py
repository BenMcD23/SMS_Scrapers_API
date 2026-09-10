"""add attendance to staff

Revision ID: h4i5j6k7l8m9
Revises: h3i4j5k6l7m8
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h4i5j6k7l8m9'
down_revision: Union[str, Sequence[str], None] = 'h3i4j5k6l7m8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Staff'"
    ))}
    if 'attendance' not in cols:
        op.add_column('Staff', sa.Column('attendance', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('Staff', 'attendance')
