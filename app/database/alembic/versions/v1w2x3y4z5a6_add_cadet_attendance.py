"""add cadet attendance

Revision ID: v1w2x3y4z5a6
Revises: u1v2w3x4y5z6
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'v1w2x3y4z5a6'
down_revision: Union[str, Sequence[str], None] = 'u1v2w3x4y5z6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}

    if 'Cadet_Attendance' not in tables:
        op.create_table(
            'Cadet_Attendance',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('date', sa.DateTime(), nullable=False),
            sa.Column('register_type', sa.Text(), nullable=True),
            sa.Column('status', sa.Text(), nullable=True),
            sa.Column('unit', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_Cadet_Attendance_cadet_id', 'Cadet_Attendance', ['cadet_id'])


def downgrade() -> None:
    op.drop_index('ix_Cadet_Attendance_cadet_id', table_name='Cadet_Attendance')
    op.drop_table('Cadet_Attendance')
