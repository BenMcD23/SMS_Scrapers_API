"""appointment applications (staff cadet and uniformed adult staff checklists)

Also merges the two heads (activity logs and dashboard layouts) that both
branched from m1s2s3n4g5c6, so `upgrade head` has one target again.

Revision ID: a2p3p4l5i6c7
Revises: a1c2t3l4o5g6, d4s5h6l7a8y9
Create Date: 2026-10-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2p3p4l5i6c7'
down_revision: Union[str, Sequence[str], None] = ('a1c2t3l4o5g6', 'd4s5h6l7a8y9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Appointment_Applications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=True),
        sa.Column('applicant_name', sa.Text(), nullable=True),
        sa.Column('route', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('started_by', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cadet_id'),
    )


def downgrade() -> None:
    op.drop_table('Appointment_Applications')
