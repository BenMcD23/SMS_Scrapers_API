"""add cadet medical and dietary tables

Revision ID: h1i2j3k4l5m6
Revises: 099f806dc621
Create Date: 2026-06-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'h1i2j3k4l5m6'
down_revision = '099f806dc621'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'Cadet_Medical',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('allergy_name', sa.Text(), nullable=False),
        sa.Column('auto_injector', sa.Text(), nullable=False, server_default='No'),
        sa.Column('severity', sa.Text(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'Cadet_Dietary',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cadet_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('Cadet_Dietary')
    op.drop_table('Cadet_Medical')
