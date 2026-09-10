"""add cadet absences and inspection sheets

Revision ID: o1p2q3r4s5t6
Revises: n2o3p4q5r6s7
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'o1p2q3r4s5t6'
down_revision: Union[str, Sequence[str], None] = 'n2o3p4q5r6s7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}

    if 'Cadet_Absences' not in tables:
        op.create_table(
            'Cadet_Absences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('date_from', sa.DateTime(), nullable=False),
            sa.Column('date_to', sa.DateTime(), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('scraped_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'Inspection_Sheets' not in tables:
        op.create_table(
            'Inspection_Sheets',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('date', sa.DateTime(), nullable=False),
            sa.Column('submitted_by', sa.Text(), nullable=True),
            sa.Column('submitted_at', sa.DateTime(), nullable=False),
            sa.Column('data', sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    op.drop_table('Inspection_Sheets')
    op.drop_table('Cadet_Absences')
