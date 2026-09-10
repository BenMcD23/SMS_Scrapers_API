"""add classification column to cadets

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-06-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'i2j3k4l5m6n7'
down_revision = 'h1i2j3k4l5m6'
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent — the column was previously dropped (migration 44a52e3ee192) and
    # may or may not exist depending on history.
    conn = op.get_bind()
    cols = [row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Cadets'"
    ))]
    if 'classification' not in cols:
        op.add_column('Cadets', sa.Column('classification', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('Cadets', 'classification')
