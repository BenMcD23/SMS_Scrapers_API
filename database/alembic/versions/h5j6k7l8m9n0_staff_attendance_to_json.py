"""staff attendance int -> json (per month)

Revision ID: h5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h5j6k7l8m9n0'
down_revision: Union[str, Sequence[str], None] = 'h4i5j6k7l8m9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _attendance_type(conn) -> str | None:
    row = conn.execute(sa.text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='Staff' AND column_name='attendance'"
    )).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()
    dtype = _attendance_type(conn)
    # Drop the old integer column (per-half total) if present; the new shape is
    # a per-month JSON map and old values aren't convertible/worth keeping.
    if dtype is not None and dtype not in ('json', 'jsonb'):
        op.drop_column('Staff', 'attendance')
        dtype = None
    if dtype is None:
        op.add_column('Staff', sa.Column('attendance', sa.JSON(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    dtype = _attendance_type(conn)
    if dtype in ('json', 'jsonb'):
        op.drop_column('Staff', 'attendance')
    op.add_column('Staff', sa.Column('attendance', sa.Integer(), nullable=True))
