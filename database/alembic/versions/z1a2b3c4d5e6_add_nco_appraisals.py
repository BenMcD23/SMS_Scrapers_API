"""add nco appraisals

Revision ID: z1a2b3c4d5e6
Revises: y1z2a3b4c5d6
Create Date: 2026-08-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'z1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'y1z2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()

    if 'NCO_Appraisals' not in tables:
        op.create_table(
            'NCO_Appraisals',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('author_id', sa.Integer(), nullable=True),
            sa.Column('appraisal_date', sa.DateTime(), nullable=False),
            sa.Column('nco_name', sa.Text(), nullable=False, server_default=''),
            sa.Column('age', sa.Text(), nullable=False, server_default=''),
            sa.Column('attendance', sa.Text(), nullable=False, server_default=''),
            sa.Column('general_observations', sa.Text(), nullable=False, server_default=''),
            sa.Column('effectiveness_in_role', sa.Text(), nullable=False, server_default=''),
            sa.Column('strengths', sa.Text(), nullable=False, server_default=''),
            sa.Column('weaknesses', sa.Text(), nullable=False, server_default=''),
            sa.Column('targets', sa.Text(), nullable=False, server_default=''),
            sa.Column('next_review_months', sa.Integer(), nullable=False, server_default='12'),
            sa.Column('next_review_date', sa.DateTime(), nullable=False),
            sa.Column('cause_for_concern', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('extend_probation', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('generated_by', sa.Text(), nullable=True),
            sa.Column('emailed_at', sa.DateTime(), nullable=True),
            sa.Column('emailed_to', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['author_id'], ['Users.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_nco_appraisals_cadet_id', 'NCO_Appraisals', ['cadet_id'])

    if 'NCO_Appraisal_Reminders' not in tables:
        op.create_table(
            'NCO_Appraisal_Reminders',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('due_date', sa.DateTime(), nullable=False),
            sa.Column('note', sa.Text(), nullable=False, server_default=''),
            sa.Column('created_by_name', sa.Text(), nullable=False),
            sa.Column('created_by_email', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_nco_appraisal_reminders_cadet_id', 'NCO_Appraisal_Reminders', ['cadet_id']
        )


def downgrade() -> None:
    op.drop_index('ix_nco_appraisal_reminders_cadet_id', table_name='NCO_Appraisal_Reminders')
    op.drop_table('NCO_Appraisal_Reminders')
    op.drop_index('ix_nco_appraisals_cadet_id', table_name='NCO_Appraisals')
    op.drop_table('NCO_Appraisals')
