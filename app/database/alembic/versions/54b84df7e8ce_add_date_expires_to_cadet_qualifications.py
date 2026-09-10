"""add date_expires to cadet qualifications

Revision ID: 54b84df7e8ce
Revises: 93a36b6017c8
Create Date: 2026-03-16 13:43:10.536734

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '54b84df7e8ce'
down_revision: Union[str, Sequence[str], None] = '93a36b6017c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Cadet_Qualifications'"
    ))}
    if 'date_expires' not in cols:
        op.add_column('Cadet_Qualifications', sa.Column('date_expires', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('Cadet_Qualifications', 'date_expires')
