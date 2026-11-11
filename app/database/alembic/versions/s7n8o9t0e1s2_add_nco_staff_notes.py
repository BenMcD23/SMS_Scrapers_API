"""add NCO staff notes

Staff-only observations about cadet NCOs, which feed the NCO appraisals.

Revision ID: s7n8o9t0e1s2
Revises: t7r8a9i0n1g2
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's7n8o9t0e1s2'
down_revision: Union[str, Sequence[str], None] = 't7r8a9i0n1g2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'NCO_Staff_Notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=True),
        sa.Column('cadet_name', sa.Text(), nullable=False, server_default=''),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('note_date', sa.DateTime(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False, server_default='typed'),
        sa.Column('appraisal_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['author_id'], ['Users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['appraisal_id'], ['NCO_Appraisals.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_NCO_Staff_Notes_cadet_id', 'NCO_Staff_Notes', ['cadet_id'])
    op.create_index('ix_NCO_Staff_Notes_author_id', 'NCO_Staff_Notes', ['author_id'])
    op.create_index('ix_NCO_Staff_Notes_appraisal_id', 'NCO_Staff_Notes', ['appraisal_id'])


def downgrade() -> None:
    op.drop_index('ix_NCO_Staff_Notes_appraisal_id', table_name='NCO_Staff_Notes')
    op.drop_index('ix_NCO_Staff_Notes_author_id', table_name='NCO_Staff_Notes')
    op.drop_index('ix_NCO_Staff_Notes_cadet_id', table_name='NCO_Staff_Notes')
    op.drop_table('NCO_Staff_Notes')
