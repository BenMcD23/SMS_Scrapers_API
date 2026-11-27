"""parade-night attendance reports

Parade_Report_Settings switches the 21:30 Wednesday/Friday run on and holds
whose Bader credentials it scrapes with. Parade_Report_Recipients is who it is
emailed to and who (besides staff) may read past reports. Parade_Reports and
Parade_Report_Entries are each night's cadet register as it was sent.

Revision ID: p1a2r3a4d5e6
Revises: s1t2n3s4n5s6
Create Date: 2026-11-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p1a2r3a4d5e6'
down_revision: Union[str, Sequence[str], None] = 's1t2n3s4n5s6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Parade_Report_Settings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('Users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_Parade_Report_Settings_user_id', 'Parade_Report_Settings', ['user_id'])

    op.create_table(
        'Parade_Report_Recipients',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('email', sa.Text(), nullable=False, unique=True),
        sa.Column('added_by', sa.Text(), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'Parade_Reports',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('parade_date', sa.Date(), nullable=False, unique=True),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('generated_by', sa.Text(), nullable=False),
        sa.Column('emailed_at', sa.DateTime(), nullable=True),
        sa.Column('emailed_to', sa.JSON(), nullable=True),
    )

    op.create_table(
        'Parade_Report_Entries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('Parade_Reports.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('cadet_cin', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('rank', sa.Text(), nullable=True),
        sa.Column('flight', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=True),
        sa.Column('state', sa.Text(), nullable=False),
    )
    op.create_index('ix_Parade_Report_Entries_report_id', 'Parade_Report_Entries', ['report_id'])


def downgrade() -> None:
    op.drop_table('Parade_Report_Entries')
    op.drop_table('Parade_Reports')
    op.drop_table('Parade_Report_Recipients')
    op.drop_table('Parade_Report_Settings')
