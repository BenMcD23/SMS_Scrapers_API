"""assessment and inspection sheets saved on the server while being filled in

Revision ID: a5d6r7a8f9t0
Revises: p1r2o3t4e5c6
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5d6r7a8f9t0'
down_revision: Union[str, Sequence[str], None] = 'p1r2o3t4e5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Assessment_Drafts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('owner', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('saved_at', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner', 'kind', name='uq_assessment_draft_owner_kind'),
    )


def downgrade() -> None:
    op.drop_table('Assessment_Drafts')
