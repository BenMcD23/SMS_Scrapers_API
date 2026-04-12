"""cin to biginteger

Revision ID: f7g8h9i0j1k2
Revises: c1d2e3f4a5b6
Create Date: 2026-04-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7g8h9i0j1k2'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade():
    # Alter FK columns before the PK they reference
    op.alter_column('Cadet_Qualifications', 'cadet_id',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False)
    op.alter_column('Cadet_Events', 'cadet_id',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False)
    op.alter_column('Assessment_Sheets', 'cadet_id',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False)
    op.alter_column('Stores_Orders', 'cadet_id',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False)
    # Alter the PK itself
    op.alter_column('Cadets', 'cin',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False)


def downgrade():
    op.alter_column('Cadets', 'cin',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False)
    op.alter_column('Cadet_Qualifications', 'cadet_id',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False)
    op.alter_column('Cadet_Events', 'cadet_id',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False)
    op.alter_column('Assessment_Sheets', 'cadet_id',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False)
    op.alter_column('Stores_Orders', 'cadet_id',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False)
