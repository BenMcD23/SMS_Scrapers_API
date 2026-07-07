"""add attachment check quals table and has_attachment column

Revision ID: k1l2m3n4o5p6
Revises: j1k2l3m4n5o6
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'k1l2m3n4o5p6'
down_revision: Union[str, Sequence[str], None] = 'j1k2l3m4n5o6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if "Attachment_Check_Quals" not in insp.get_table_names():
        op.create_table(
            "Attachment_Check_Quals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("qual_name", sa.Text(), nullable=False, unique=True),
        )

    cols = [c["name"] for c in insp.get_columns("Cadet_Qualifications")]
    if "has_attachment" not in cols:
        op.add_column("Cadet_Qualifications", sa.Column("has_attachment", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    insp = sa.inspect(conn)

    cols = [c["name"] for c in insp.get_columns("Cadet_Qualifications")]
    if "has_attachment" in cols:
        op.drop_column("Cadet_Qualifications", "has_attachment")

    if "Attachment_Check_Quals" in insp.get_table_names():
        op.drop_table("Attachment_Check_Quals")
