"""staff-added QR code sections, subjects and their lesson codes

Revision ID: q2r3c4s5u6b7
Revises: s1e2s3k4i5t6
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'q2r3c4s5u6b7'
down_revision: Union[str, Sequence[str], None] = 's1e2s3k4i5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Theory_Sections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'Theory_Subjects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('parts', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_table(
        'Theory_Subject_Codes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('subject_key', sa.Text(), nullable=False),
        sa.Column('part', sa.Integer(), nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.UniqueConstraint('subject_key', 'part', name='uq_theory_subject_code_part'),
    )


def downgrade() -> None:
    op.drop_table('Theory_Subject_Codes')
    op.drop_table('Theory_Subjects')
    op.drop_table('Theory_Sections')
