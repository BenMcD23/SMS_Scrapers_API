"""add the room to each programme record session

The old docs' second page (the roomings) says where each session ran; the
import now keeps it, and so does a published builder month, so suggestions
can learn which room each activity uses.

Revision ID: r0o1m2s3e4s5
Revises: p7b8u9i0l1d2
Create Date: 2026-11-27 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r0o1m2s3e4s5'
down_revision: Union[str, Sequence[str], None] = 'p7b8u9i0l1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Programme_Sessions', sa.Column('room', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('Programme_Sessions', 'room')
