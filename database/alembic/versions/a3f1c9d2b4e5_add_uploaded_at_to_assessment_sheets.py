"""add uploaded_at to assessment sheets

Revision ID: a3f1c9d2b4e5
Revises: f9a8b7c6d5e4
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f1c9d2b4e5'
down_revision: Union[str, Sequence[str], None] = 'f9a8b7c6d5e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Assessment_Sheets'"
    ))}
    if 'uploaded_at' not in cols:
        op.add_column('Assessment_Sheets',
            sa.Column('uploaded_at', sa.DateTime(), nullable=True))
        conn.execute(sa.text(
            'UPDATE "Assessment_Sheets" SET uploaded_at = created_at'
            ' WHERE uploaded = true AND uploaded_at IS NULL'
        ))


def downgrade() -> None:
    op.drop_column('Assessment_Sheets', 'uploaded_at')
