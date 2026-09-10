"""merge_uploaded_at_and_ready_to_collect

Revision ID: 099f806dc621
Revises: a3f1c9d2b4e5, g1h2i3j4k5l6
Create Date: 2026-06-11 17:15:27.119824

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '099f806dc621'
down_revision: Union[str, Sequence[str], None] = ('a3f1c9d2b4e5', 'g1h2i3j4k5l6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
