"""activity log entry sign-off

Revision ID: s2i3g4n5o6f7
Revises: a2p3p4l5i6c7
Create Date: 2026-10-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's2i3g4n5o6f7'
down_revision: Union[str, Sequence[str], None] = 'a2p3p4l5i6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Activity_Log_Entries', sa.Column('signed_off_initials', sa.Text(), nullable=True))
    op.add_column('Activity_Log_Entries', sa.Column('signed_off_by', sa.Text(), nullable=True))
    op.add_column('Activity_Log_Entries', sa.Column('signed_off_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('Activity_Log_Entries') as batch:
        batch.drop_column('signed_off_at')
        batch.drop_column('signed_off_by')
        batch.drop_column('signed_off_initials')
