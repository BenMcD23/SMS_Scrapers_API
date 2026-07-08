"""add ban notifications table

Tracks which (banned cadet, event) pairs staff have already been emailed about,
so a given pairing is only alerted once. Keyed on event title because the
Cadet_Events / All_Events rows are wiped and recreated on every event scrape.

Any pre-existing Ban_Notifications table (an earlier, unused cadet_event_id
shape that was never migrated) is dropped and recreated.

Revision ID: m1n2o3p4q5r6
Revises: l1m2n3o4p5q6
Create Date: 2026-07-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm1n2o3p4q5r6'
down_revision: Union[str, Sequence[str], None] = 'l1m2n3o4p5q6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "Ban_Notifications"')
    op.create_table(
        'Ban_Notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('event_title', sa.Text(), nullable=False),
        sa.Column('notified_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cadet_id', 'event_title', name='uq_ban_notif_cadet_event'),
    )


def downgrade() -> None:
    op.drop_table('Ban_Notifications')
