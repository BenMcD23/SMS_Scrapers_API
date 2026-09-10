"""add cadet leaving process

Revision ID: x1y2z3a4b5c6
Revises: w1x2y3z4a5b6
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'x1y2z3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'w1x2y3z4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}

    if 'Cadet_Leaving_Process' not in tables:
        op.create_table(
            'Cadet_Leaving_Process',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('sent_at', sa.DateTime(), nullable=False),
            sa.Column('sent_to', sa.Text(), nullable=False),
            sa.Column('reply_to', sa.Text(), nullable=False),
            sa.Column('sent_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            # One open process per cadet; cancelling deletes the row.
            sa.UniqueConstraint('cadet_id', name='uq_leaving_process_cadet'),
        )


def downgrade() -> None:
    op.drop_table('Cadet_Leaving_Process')
