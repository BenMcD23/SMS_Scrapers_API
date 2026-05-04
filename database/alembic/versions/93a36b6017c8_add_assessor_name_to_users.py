"""add assessor_name to users

Revision ID: 93a36b6017c8
Revises: 44a52e3ee192
Create Date: 2026-03-16 12:04:31.108476

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '93a36b6017c8'
down_revision: Union[str, Sequence[str], None] = '44a52e3ee192'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Users'"
    ))}
    if 'assessor_name' not in cols:
        op.add_column('Users', sa.Column('assessor_name', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('Users', 'assessor_name')
