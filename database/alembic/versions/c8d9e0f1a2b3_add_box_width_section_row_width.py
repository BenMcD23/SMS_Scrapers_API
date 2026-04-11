"""add box width and section row/width

Revision ID: c8d9e0f1a2b3
Revises: f1a2b3c4d5e6
Create Date: 2026-04-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('Stores_Boxes',
        sa.Column('box_width', sa.Integer(), nullable=True, server_default='100'))
    op.add_column('Stores_Sections',
        sa.Column('section_row', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('Stores_Sections',
        sa.Column('section_width', sa.Integer(), nullable=True, server_default='100'))


def downgrade() -> None:
    op.drop_column('Stores_Sections', 'section_width')
    op.drop_column('Stores_Sections', 'section_row')
    op.drop_column('Stores_Boxes', 'box_width')
