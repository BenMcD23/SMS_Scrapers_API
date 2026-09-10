"""merge gained where and badge order list entry audit heads

Revision ID: e8f9a0b1c2d3
Revises: a5b6c7d8e9f0, d7e8f9a0b1c2
Create Date: 2026-09-09 08:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, Sequence[str], None] = ('a5b6c7d8e9f0', 'd7e8f9a0b1c2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
