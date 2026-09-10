"""add per-entry ordered/received audit to badge order list, drop batching

Badges on the order list used to move as a whole batch (a Badge_Order_List
locked in one go via "mark ordered"). They now move one at a time through
queued -> ordered -> received, each stamped with who did it, so the list
table itself is no longer needed — just the entries.

Revision ID: d7e8f9a0b1c2
Revises: b5c6d7e8f9a0
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Badge_Order_List_Entries'"
    ))}

    if 'added_by' not in cols:
        op.add_column('Badge_Order_List_Entries', sa.Column('added_by', sa.Text(), nullable=True))
    if 'ordered_at' not in cols:
        op.add_column('Badge_Order_List_Entries', sa.Column('ordered_at', sa.DateTime(), nullable=True))
    if 'ordered_by' not in cols:
        op.add_column('Badge_Order_List_Entries', sa.Column('ordered_by', sa.Text(), nullable=True))
    if 'received_at' not in cols:
        op.add_column('Badge_Order_List_Entries', sa.Column('received_at', sa.DateTime(), nullable=True))
    if 'received_by' not in cols:
        op.add_column('Badge_Order_List_Entries', sa.Column('received_by', sa.Text(), nullable=True))

    if 'list_id' in cols:
        inspector = sa.inspect(conn)
        for fk in inspector.get_foreign_keys('Badge_Order_List_Entries'):
            if 'list_id' in fk.get('constrained_columns', []) and fk.get('name'):
                op.drop_constraint(fk['name'], 'Badge_Order_List_Entries', type_='foreignkey')
        op.drop_column('Badge_Order_List_Entries', 'list_id')

    if 'Badge_Order_Lists' in sa.inspect(conn).get_table_names():
        op.drop_table('Badge_Order_Lists')


def downgrade() -> None:
    op.create_table(
        'Badge_Order_Lists',
        sa.Column('id',         sa.Integer(),  nullable=False, autoincrement=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ordered_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column('Badge_Order_List_Entries', sa.Column('list_id', sa.Integer(), nullable=True))
    op.drop_column('Badge_Order_List_Entries', 'received_by')
    op.drop_column('Badge_Order_List_Entries', 'received_at')
    op.drop_column('Badge_Order_List_Entries', 'ordered_by')
    op.drop_column('Badge_Order_List_Entries', 'ordered_at')
    op.drop_column('Badge_Order_List_Entries', 'added_by')
