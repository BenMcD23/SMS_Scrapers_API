"""add assessor_name to user_profiles

Revision ID: e5f6a7b8c9d0
Revises: cd453e5dc0b5
Create Date: 2026-04-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'cd453e5dc0b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='User_Profiles'"
    ))}
    if 'assessor_name' not in cols:
        op.add_column('User_Profiles', sa.Column('assessor_name', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('User_Profiles', 'assessor_name')
