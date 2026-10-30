"""add assistant chats

Revision ID: a7s8i9s0t1c2
Revises: w1h2t3s4c5d6
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7s8i9s0t1c2'
down_revision: Union[str, Sequence[str], None] = 'w1h2t3s4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Assistant_Chats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('pinned', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_Assistant_Chats_email', 'Assistant_Chats', ['email'])
    op.create_table(
        'Assistant_Messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('feedback', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['Assistant_Chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_Assistant_Messages_chat_id', 'Assistant_Messages', ['chat_id'])


def downgrade() -> None:
    op.drop_index('ix_Assistant_Messages_chat_id', table_name='Assistant_Messages')
    op.drop_table('Assistant_Messages')
    op.drop_index('ix_Assistant_Chats_email', table_name='Assistant_Chats')
    op.drop_table('Assistant_Chats')
