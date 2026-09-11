"""merge branches

Revision ID: cd453e5dc0b5
Revises: b2c3d4e5f6a1, c1d2e3f4a5b6, d4e5f6a7b8c9
Create Date: 2026-04-07 18:47:23.254021

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cd453e5dc0b5'
down_revision: Union[str, Sequence[str], None] = ('b2c3d4e5f6a1', 'c1d2e3f4a5b6', 'd4e5f6a7b8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
