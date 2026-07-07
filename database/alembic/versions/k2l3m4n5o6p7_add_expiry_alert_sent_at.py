"""add expiry_alert_sent_at to cadet qualifications

Records when the 3-month pre-expiry alert email has been sent for a
qualification, so each cadet+qualification is notified exactly once.

Revision ID: k2l3m4n5o6p7
Revises: k1l2m3n4o5p6
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k2l3m4n5o6p7'
down_revision: Union[str, Sequence[str], None] = 'k1l2m3n4o5p6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Cadet_Qualifications'"
    ))}
    if 'expiry_alert_sent_at' not in cols:
        op.add_column('Cadet_Qualifications', sa.Column('expiry_alert_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('Cadet_Qualifications', 'expiry_alert_sent_at')
