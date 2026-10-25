"""merge activity logs audit events and dashboard layouts

Revision ID: m2e3r4g5e6h7
Revises: a1c2t3l4o5g6, a2u3d4i5t6e7, d4s5h6l7a8y9
Create Date: 2026-10-24 10:27:16.603468

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm2e3r4g5e6h7'
down_revision: Union[str, Sequence[str], None] = ('a1c2t3l4o5g6', 'a2u3d4i5t6e7', 'd4s5h6l7a8y9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
