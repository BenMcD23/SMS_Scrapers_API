"""add recruit sign-up settings

Whether the public recruitment-evening sign-up page takes sign-ups.

Revision ID: r5s6i7g8n9u0
Revises: a5d6r7a8f9t0
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r5s6i7g8n9u0'
down_revision: Union[str, Sequence[str], None] = 'a5d6r7a8f9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Recruit_Signup_Settings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('open_until', sa.DateTime(), nullable=True),
        sa.Column('opened_by', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('Recruit_Signup_Settings')
