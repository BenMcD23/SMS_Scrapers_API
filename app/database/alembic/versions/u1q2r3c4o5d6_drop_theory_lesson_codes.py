"""drop Theory_Lesson_Codes

The lesson QR codes are now fixed in core/lesson_codes.py, so no database
state, restore or failover can change them. The table that used to hold them
is unused.

Revision ID: u1q2r3c4o5d6
Revises: t1h2e3o4r5y6
Create Date: 2026-10-15 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'u1q2r3c4o5d6'
down_revision: Union[str, Sequence[str], None] = 't1h2e3o4r5y6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if 'Theory_Lesson_Codes' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('Theory_Lesson_Codes')


def downgrade() -> None:
    op.create_table(
        'Theory_Lesson_Codes',
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('lesson_key', sa.Text(), nullable=False),
        sa.Column('part', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('code'),
        sa.UniqueConstraint('lesson_key', 'part', name='uq_theory_code_lesson_part'),
    )
