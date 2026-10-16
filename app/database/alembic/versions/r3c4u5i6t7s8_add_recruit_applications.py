"""add recruit applications

New recruits' joining forms (RAFAC 3822A/3822H), filled in online by parents.

Revision ID: r3c4u5i6t7s8
Revises: a2g3i4v5e6n7
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r3c4u5i6t7s8'
down_revision: Union[str, Sequence[str], None] = 'a2g3i4v5e6n7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Recruit_Applications',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cadet_first_name', sa.Text(), nullable=False),
        sa.Column('cadet_last_name', sa.Text(), nullable=False),
        sa.Column('parent_name', sa.Text(), nullable=False),
        sa.Column('parent_email', sa.Text(), nullable=False),
        sa.Column('token', sa.Text(), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('opened_at', sa.DateTime(), nullable=True),
        sa.Column('last_saved_at', sa.DateTime(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('parent_signature', sa.LargeBinary(), nullable=True),
        sa.Column('cadet_signature', sa.LargeBinary(), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('Recruit_Applications')
