"""Push_Subscriptions

Browsers and installed apps that take push notifications, with the topics each
one wants.

Revision ID: p1u2s3h4s5b6
Revises: m1s2s3n4g5c6
Create Date: 2026-10-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p1u2s3h4s5b6'
down_revision: Union[str, Sequence[str], None] = 'm1s2s3n4g5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Push_Subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False, unique=True),
        sa.Column('p256dh', sa.Text(), nullable=False),
        sa.Column('auth', sa.Text(), nullable=False),
        sa.Column('topics', sa.JSON(), nullable=False),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_Push_Subscriptions_email', 'Push_Subscriptions', ['email'])


def downgrade() -> None:
    op.drop_index('ix_Push_Subscriptions_email', table_name='Push_Subscriptions')
    op.drop_table('Push_Subscriptions')
