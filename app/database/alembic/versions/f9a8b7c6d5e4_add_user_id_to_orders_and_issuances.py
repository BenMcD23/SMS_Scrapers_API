"""add user_id to stores orders and issuances

Revision ID: b1c2d3e4f5a6
Revises: e1f2a3b4c5d6
Create Date: 2026-05-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f9a8b7c6d5e4'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # --- Stores_Orders ---
    order_cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Stores_Orders'"
    ))}
    if 'user_id' not in order_cols:
        op.add_column('Stores_Orders',
            sa.Column('user_id', sa.Integer(),
                      sa.ForeignKey('Users.id', ondelete='SET NULL'),
                      nullable=True))
    # Make cadet_id nullable if it isn't already
    op.alter_column('Stores_Orders', 'cadet_id', nullable=True)

    # --- Stores_Item_Issuances ---
    issuance_cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Stores_Item_Issuances'"
    ))}
    if 'user_id' not in issuance_cols:
        op.add_column('Stores_Item_Issuances',
            sa.Column('user_id', sa.Integer(),
                      sa.ForeignKey('Users.id', ondelete='CASCADE'),
                      nullable=True))
    op.alter_column('Stores_Item_Issuances', 'cadet_id', nullable=True)


def downgrade() -> None:
    op.alter_column('Stores_Item_Issuances', 'cadet_id', nullable=False)
    op.drop_column('Stores_Item_Issuances', 'user_id')
    op.alter_column('Stores_Orders', 'cadet_id', nullable=False)
    op.drop_column('Stores_Orders', 'user_id')
