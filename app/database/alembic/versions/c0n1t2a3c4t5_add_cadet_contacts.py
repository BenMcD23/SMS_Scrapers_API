"""add cadets' Bader contact methods and their parent contacts

The cadet scraper now reads the cadet's own contact methods off their profile
and each contact (parent, guardian...) off the Contacts tab, for the contact
details audit.

Revision ID: c0n1t2a3c4t5
Revises: r0o1m2s3e4s5
Create Date: 2026-11-27 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c0n1t2a3c4t5'
down_revision: Union[str, Sequence[str], None] = 'r0o1m2s3e4s5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Cadets', sa.Column('contact_methods', sa.JSON(), nullable=True))
    op.add_column('Cadets', sa.Column('contacts_checked_at', sa.DateTime(), nullable=True))
    op.create_table(
        'Cadet_Contacts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cadet_id', sa.BigInteger(), sa.ForeignKey('Cadets.cin', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('relationship', sa.Text(), nullable=True),
        sa.Column('next_of_kin', sa.Boolean(), nullable=False),
        sa.Column('contact_methods', sa.JSON(), nullable=False),
    )
    op.create_index('ix_Cadet_Contacts_cadet_id', 'Cadet_Contacts', ['cadet_id'])


def downgrade() -> None:
    op.drop_index('ix_Cadet_Contacts_cadet_id', table_name='Cadet_Contacts')
    op.drop_table('Cadet_Contacts')
    op.drop_column('Cadets', 'contacts_checked_at')
    op.drop_column('Cadets', 'contact_methods')
