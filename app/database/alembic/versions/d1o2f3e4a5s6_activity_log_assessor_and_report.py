"""activity log: DofE number, final-report assessor and report sign-off

Revision ID: d1o2f3e4a5s6
Revises: p1r2o3g4r5m6
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1o2f3e4a5s6'
down_revision: Union[str, Sequence[str], None] = 'p1r2o3g4r5m6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('Activity_Log_Enrolments') as batch:
        batch.add_column(sa.Column('dofe_number', sa.Text(), nullable=True))
        batch.add_column(sa.Column('assessor_name', sa.Text(), nullable=True))
        batch.add_column(sa.Column('assessor_staff_cin', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('report_done_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('report_done_by', sa.Text(), nullable=True))
        batch.create_foreign_key('fk_activity_log_assessor_staff', 'Staff',
                                 ['assessor_staff_cin'], ['cin'], ondelete='SET NULL')
    op.create_index(op.f('ix_Activity_Log_Enrolments_assessor_staff_cin'), 'Activity_Log_Enrolments',
                    ['assessor_staff_cin'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_Activity_Log_Enrolments_assessor_staff_cin'), table_name='Activity_Log_Enrolments')
    with op.batch_alter_table('Activity_Log_Enrolments') as batch:
        batch.drop_constraint('fk_activity_log_assessor_staff', type_='foreignkey')
        batch.drop_column('report_done_by')
        batch.drop_column('report_done_at')
        batch.drop_column('assessor_staff_cin')
        batch.drop_column('assessor_name')
        batch.drop_column('dofe_number')
