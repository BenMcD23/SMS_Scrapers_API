"""Dashboard_Layouts

Saves each OC's drag-and-drop order for the OC dashboard's sections.

Revision ID: d4s5h6l7a8y9
Revises: m1s2s3n4g5c6
Create Date: 2026-10-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4s5h6l7a8y9'
down_revision: Union[str, Sequence[str], None] = 'm1s2s3n4g5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Dashboard_Layouts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('Users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dashboard', sa.Text(), nullable=False),
        sa.Column('sections', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'dashboard', name='uq_dashboard_layout_user'),
    )


def downgrade() -> None:
    op.drop_table('Dashboard_Layouts')
