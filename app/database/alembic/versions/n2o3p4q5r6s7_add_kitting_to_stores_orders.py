"""add kitting to stores orders

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Branch Labels: None
Depends On: None

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'n2o3p4q5r6s7'
down_revision: Union[str, Sequence[str], None] = 'm1n2o3p4q5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Stores_Orders'"
    ))}
    if 'kitting' not in cols:
        op.add_column('Stores_Orders', sa.Column('kitting', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('Stores_Orders', 'kitting')
