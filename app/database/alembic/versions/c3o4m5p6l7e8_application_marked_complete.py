"""appointment applications: marked complete by hand

Revision ID: c3o4m5p6l7e8
Revises: p1u2s3h4s5b6
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3o4m5p6l7e8'
down_revision: Union[str, Sequence[str], None] = 'p1u2s3h4s5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Appointment_Applications', sa.Column('completed_at', sa.DateTime(), nullable=True))
    op.add_column('Appointment_Applications', sa.Column('completed_by', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('Appointment_Applications') as batch:
        batch.drop_column('completed_by')
        batch.drop_column('completed_at')
