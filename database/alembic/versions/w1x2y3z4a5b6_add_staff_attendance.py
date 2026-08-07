"""add staff attendance

Revision ID: w1x2y3z4a5b6
Revises: v1w2x3y4z5a6
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'w1x2y3z4a5b6'
down_revision: Union[str, Sequence[str], None] = 'v1w2x3y4z5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}

    if 'Staff_Attendance' not in tables:
        op.create_table(
            'Staff_Attendance',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('staff_id', sa.BigInteger(), nullable=False),
            sa.Column('date', sa.DateTime(), nullable=False),
            sa.Column('register_type', sa.Text(), nullable=True),
            sa.Column('status', sa.Text(), nullable=True),
            sa.Column('unit', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['staff_id'], ['Staff.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_Staff_Attendance_staff_id', 'Staff_Attendance', ['staff_id'])


def downgrade() -> None:
    op.drop_index('ix_Staff_Attendance_staff_id', table_name='Staff_Attendance')
    op.drop_table('Staff_Attendance')
