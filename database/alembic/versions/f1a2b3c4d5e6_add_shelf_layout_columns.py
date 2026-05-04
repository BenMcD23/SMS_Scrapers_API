"""add shelf layout columns to boxes and sections

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-04-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = ('e5f6a7b8c9d0', '3a27d5260176')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    box_cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Stores_Boxes'"
    ))}
    sec_cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Stores_Sections'"
    ))}

    if 'shelf_level' not in box_cols:
        op.add_column('Stores_Boxes',
            sa.Column('shelf_level', sa.Integer(), nullable=True, server_default='1'))
    if 'shelf_position' not in box_cols:
        op.add_column('Stores_Boxes',
            sa.Column('shelf_position', sa.Integer(), nullable=True, server_default='0'))
    if 'top_end' not in box_cols:
        op.add_column('Stores_Boxes',
            sa.Column('top_end', sa.Text(), nullable=True, server_default='left'))

    if 'position' not in sec_cols:
        op.add_column('Stores_Sections',
            sa.Column('position', sa.Integer(), nullable=True, server_default='0'))

    op.execute("""
        UPDATE "Stores_Boxes"
        SET shelf_position = (
            SELECT COUNT(*) FROM "Stores_Boxes" b2
            WHERE b2.shelf_level <= 1 AND b2.id < "Stores_Boxes".id
        )
        WHERE shelf_level = 1 OR shelf_level IS NULL
    """)

    op.execute("""
        UPDATE "Stores_Sections"
        SET position = (
            SELECT COUNT(*) FROM "Stores_Sections" ss2
            WHERE ss2.box_id = "Stores_Sections".box_id AND ss2.id < "Stores_Sections".id
        )
    """)


def downgrade() -> None:
    op.drop_column('Stores_Sections', 'position')
    op.drop_column('Stores_Boxes', 'top_end')
    op.drop_column('Stores_Boxes', 'shelf_position')
    op.drop_column('Stores_Boxes', 'shelf_level')
