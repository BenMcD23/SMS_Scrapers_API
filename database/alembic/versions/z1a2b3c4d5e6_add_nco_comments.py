"""add nco quick comments

Revision ID: z1a2b3c4d5e6
Revises: y1z2a3b4c5d6
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'z1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'y1z2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}

    if 'NCO_Comments' not in tables:
        op.create_table(
            'NCO_Comments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('author_id', sa.Integer(), nullable=False),
            sa.Column('subject', sa.Text(), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('comment_date', sa.DateTime(), nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=True),
            sa.Column('cadet_name', sa.Text(), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['author_id'], ['Users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_NCO_Comments_cadet_id', 'NCO_Comments', ['cadet_id'])

    if 'NCO_Comment_Replies' not in tables:
        op.create_table(
            'NCO_Comment_Replies',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('comment_id', sa.Integer(), nullable=False),
            sa.Column('author_id', sa.Integer(), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['comment_id'], ['NCO_Comments.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['author_id'], ['Users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_NCO_Comment_Replies_comment_id', 'NCO_Comment_Replies', ['comment_id'])


def downgrade() -> None:
    op.drop_index('ix_NCO_Comment_Replies_comment_id', table_name='NCO_Comment_Replies')
    op.drop_table('NCO_Comment_Replies')
    op.drop_index('ix_NCO_Comments_cadet_id', table_name='NCO_Comments')
    op.drop_table('NCO_Comments')
