"""add phone_number to cadets and staff

Parade-night text numbers move off the standalone Sms_Recipients list and onto
the person they belong to. The rows themselves are matched across by
app/scripts/migrate_sms_numbers.py, which is run by hand — this only makes the
columns for it to write into.

Revision ID: a4b5c6d7e8f9
Revises: z2a3b4c5d6e7
Create Date: 2026-09-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a4b5c6d7e8f9'
down_revision = 'z2a3b4c5d6e7'
branch_labels = None
depends_on = None


def _columns(conn, table: str) -> set[str]:
    return {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
    ), {"t": table})}


def upgrade():
    conn = op.get_bind()
    if 'phone_number' not in _columns(conn, 'Cadets'):
        op.add_column('Cadets', sa.Column('phone_number', sa.Text(), nullable=True))
    if 'phone_number' not in _columns(conn, 'Staff'):
        op.add_column('Staff', sa.Column('phone_number', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('Staff', 'phone_number')
    op.drop_column('Cadets', 'phone_number')
