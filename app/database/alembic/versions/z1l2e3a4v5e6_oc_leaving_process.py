"""parent emails and adjutant removal request for the leaving process

Revision ID: z1l2e3a4v5e6
Revises: f1y2i3n4g5d6
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'z1l2e3a4v5e6'
down_revision: Union[str, Sequence[str], None] = 'f1y2i3n4g5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table: str) -> set[str]:
    return {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t"), {"t": table})}


def upgrade() -> None:
    conn = op.get_bind()

    if 'parent_emails' not in _columns(conn, 'Cadets'):
        op.add_column('Cadets', sa.Column('parent_emails', sa.Text(), nullable=True))

    existing = _columns(conn, 'Cadet_Leaving_Process')
    for name in ('adj_requested_at',):
        if name not in existing:
            op.add_column('Cadet_Leaving_Process', sa.Column(name, sa.DateTime(), nullable=True))
    for name in ('adj_requested_by', 'adj_sent_to'):
        if name not in existing:
            op.add_column('Cadet_Leaving_Process', sa.Column(name, sa.Text(), nullable=True))
    # The site no longer sends the query itself, so there is no reply-to to keep.
    op.alter_column('Cadet_Leaving_Process', 'reply_to', existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.execute('UPDATE "Cadet_Leaving_Process" SET reply_to = \'\' WHERE reply_to IS NULL')
    op.alter_column('Cadet_Leaving_Process', 'reply_to', existing_type=sa.Text(), nullable=False)
    for name in ('adj_sent_to', 'adj_requested_by', 'adj_requested_at'):
        op.drop_column('Cadet_Leaving_Process', name)
    op.drop_column('Cadets', 'parent_emails')
