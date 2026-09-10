"""add badge item quantity

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-04-18 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a9b0c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("Badge_Items")}
    if "quantity" not in cols:
        op.add_column(
            "Badge_Items",
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    op.drop_column("Badge_Items", "quantity")
