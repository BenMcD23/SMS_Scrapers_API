"""add lesson plan pdf to assessment sheets

Revision ID: g3h4i5j6k7l8
Revises: f7g8h9i0j1k2
Create Date: 2026-06-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'g3h4i5j6k7l8'
down_revision = 'f7g8h9i0j1k2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('Assessment_Sheets', sa.Column('lesson_plan_pdf', sa.LargeBinary(), nullable=True))
    op.add_column('Assessment_Sheets', sa.Column('lesson_plan_filename', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('Assessment_Sheets', 'lesson_plan_filename')
    op.drop_column('Assessment_Sheets', 'lesson_plan_pdf')
