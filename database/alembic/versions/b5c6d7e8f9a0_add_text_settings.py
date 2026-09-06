"""add Text_Settings

Holds the WhatsApp community invite link shown to anyone who has saved a number
for the parade-night texts. A single row, created on first read by
texts.settings.get_text_settings — nothing is seeded here.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b5c6d7e8f9a0'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name='Text_Settings'"
    )).first()
    if not exists:
        op.create_table(
            'Text_Settings',
            sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
            sa.Column('whatsapp_invite_url', sa.Text(), nullable=False, server_default=''),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    op.drop_table('Text_Settings')
