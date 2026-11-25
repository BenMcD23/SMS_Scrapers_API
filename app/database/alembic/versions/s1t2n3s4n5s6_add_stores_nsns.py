"""stock numbers staff have taught the stores scanner

Stores_Nsns maps a label's NSN to a catalogue item and size, for labels the
Logs Form 202 template doesn't list (the form's own lines are read from the
template, not stored).

Revision ID: s1t2n3s4n5s6
Revises: d1r2i3l4l5c6
Create Date: 2026-11-25 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's1t2n3s4n5s6'
down_revision: Union[str, Sequence[str], None] = 'd1r2i3l4l5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'Stores_Nsns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('nsn', sa.Text(), nullable=False, unique=True),
        sa.Column('item_type', sa.Text(), nullable=False),
        sa.Column('size', sa.Text(), nullable=False),
        sa.Column('added_by', sa.Text(), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('Stores_Nsns')
