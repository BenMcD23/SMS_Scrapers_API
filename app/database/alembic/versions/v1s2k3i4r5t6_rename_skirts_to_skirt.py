"""rename item type "Skirts" to "Skirt"

Revision ID: v1s2k3i4r5t6
Revises: u1q2r3c4o5d6
Create Date: 2026-10-15 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'v1s2k3i4r5t6'
down_revision: Union[str, Sequence[str], None] = 'u1q2r3c4o5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Plain renames: none of these tables has a unique key on item_type, so rows
# are kept as they are (no merging, no deleting).
TABLES = ('Stores_Items', 'Stores_Order_Items', 'Logs_Form_Entries')


def upgrade() -> None:
    for t in TABLES:
        op.execute(f'UPDATE "{t}" SET item_type = \'Skirt\' WHERE item_type = \'Skirts\'')


def downgrade() -> None:
    # Not reversible: rows that were already "Skirt" can't be told apart.
    pass
