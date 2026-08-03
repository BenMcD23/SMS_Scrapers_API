"""add audit events table

Revision ID: s1t2u3v4w5x6
Revises: r1s2t3u4v5w6
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's1t2u3v4w5x6'
down_revision: Union[str, Sequence[str], None] = 'r1s2t3u4v5w6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = [row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))]
    if 'Audit_Events' not in tables:
        op.create_table(
            'Audit_Events',
            sa.Column('id',          sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('occurred_at', sa.DateTime(), nullable=False),
            sa.Column('actor_email', sa.Text(), nullable=False),
            sa.Column('actor_role',  sa.Text(), nullable=True),
            sa.Column('action',      sa.Text(), nullable=False),
            sa.Column('resource',    sa.Text(), nullable=False),
            sa.Column('resource_id', sa.Text(), nullable=True),
            sa.Column('detail',      sa.JSON(), nullable=True),
        )
        # The retention sweep deletes by age, so occurred_at carries the index.
        op.create_index('ix_Audit_Events_occurred_at', 'Audit_Events', ['occurred_at'])


def downgrade() -> None:
    op.drop_index('ix_Audit_Events_occurred_at', table_name='Audit_Events')
    op.drop_table('Audit_Events')
