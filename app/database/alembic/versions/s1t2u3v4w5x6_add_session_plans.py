"""add NCO session plans and their attachments

Revision ID: s1t2u3v4w5x6
Revises: r1s2t3u4v5w6
Create Date: 2026-08-04 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 's1t2u3v4w5x6'
down_revision: Union[str, Sequence[str], None] = 'r1s2t3u4v5w6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if 'Session_Plans' not in tables:
        op.create_table(
            'Session_Plans',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('session_name', sa.Text(), nullable=False),
            sa.Column('session_ic', sa.Text(), nullable=False, server_default=''),
            sa.Column('session_date', sa.DateTime(), nullable=True),
            sa.Column('aim', sa.Text(), nullable=False, server_default=''),
            sa.Column('participants', sa.Text(), nullable=False, server_default=''),
            sa.Column('rooming', sa.Text(), nullable=False, server_default=''),
            sa.Column('equipment', sa.Text(), nullable=False, server_default=''),
            sa.Column('situation', sa.Text(), nullable=False, server_default=''),
            sa.Column('mission', sa.Text(), nullable=False, server_default=''),
            sa.Column('execution', sa.Text(), nullable=False, server_default=''),
            sa.Column('any_questions', sa.Text(), nullable=False, server_default=''),
            sa.Column('check_understanding', sa.Text(), nullable=False, server_default=''),
            sa.Column('timetable', sa.JSON(), nullable=False),
            sa.Column('notes', sa.Text(), nullable=False, server_default=''),
            sa.Column('status', sa.Text(), nullable=False, server_default='draft'),
            sa.Column('feedback', sa.JSON(), nullable=False),
            sa.Column('amendment_note', sa.Text(), nullable=True),
            sa.Column('author_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('submitted_at', sa.DateTime(), nullable=True),
            sa.Column('reviewed_at', sa.DateTime(), nullable=True),
            sa.Column('reviewed_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['author_id'], ['Users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Session_Plan_Attachments' not in tables:
        op.create_table(
            'Session_Plan_Attachments',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('plan_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.Text(), nullable=False),
            sa.Column('mime_type', sa.Text(), nullable=False),
            sa.Column('data', sa.LargeBinary(), nullable=False),
            sa.Column('uploaded_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['plan_id'], ['Session_Plans.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Session_Plan_Attachments')
    op.drop_table('Session_Plans')
