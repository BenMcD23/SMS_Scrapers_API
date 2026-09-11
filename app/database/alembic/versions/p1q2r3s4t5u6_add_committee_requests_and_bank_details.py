"""add committee requests, receipts, and profile bank details

Revision ID: p1q2r3s4t5u6
Revises: o1p2q3r4s5t6
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p1q2r3s4t5u6'
down_revision: Union[str, Sequence[str], None] = 'o1p2q3r4s5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    profile_cols = {c['name'] for c in inspector.get_columns('User_Profiles')} \
        if 'User_Profiles' in tables else set()
    for col in ('bank_account_name', 'bank_sort_code', 'bank_account_number'):
        if col not in profile_cols:
            op.add_column('User_Profiles', sa.Column(col, sa.Text(), nullable=True))

    if 'Committee_Requests' not in tables:
        op.create_table(
            'Committee_Requests',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('year', sa.Integer(), nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
            sa.Column('reference', sa.Text(), nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('justification', sa.Text(), nullable=False),
            sa.Column('items', sa.JSON(), nullable=False),
            sa.Column('total', sa.Float(), nullable=False),
            sa.Column('status', sa.Text(), nullable=False),
            sa.Column('rejection_reason', sa.Text(), nullable=True),
            sa.Column('pdf_data', sa.LargeBinary(), nullable=True),
            sa.Column('requester_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('sent_to_committee_at', sa.DateTime(), nullable=True),
            sa.Column('sent_to_committee_by', sa.Text(), nullable=True),
            sa.Column('decided_at', sa.DateTime(), nullable=True),
            sa.Column('decided_by', sa.Text(), nullable=True),
            sa.Column('sent_for_payment_at', sa.DateTime(), nullable=True),
            sa.Column('paid_at', sa.DateTime(), nullable=True),
            sa.Column('paid_marked_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['requester_id'], ['Users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('reference'),
            sa.UniqueConstraint('year', 'seq', name='uq_committee_request_year_seq'),
        )

    if 'Committee_Request_Receipts' not in tables:
        op.create_table(
            'Committee_Request_Receipts',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('request_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.Text(), nullable=False),
            sa.Column('mime_type', sa.Text(), nullable=False),
            sa.Column('data', sa.LargeBinary(), nullable=False),
            sa.Column('uploaded_at', sa.DateTime(), nullable=False),
            sa.Column('uploaded_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['request_id'], ['Committee_Requests.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Committee_Request_Receipts')
    op.drop_table('Committee_Requests')
    for col in ('bank_account_number', 'bank_sort_code', 'bank_account_name'):
        op.drop_column('User_Profiles', col)
