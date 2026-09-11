"""merge committee request withdrawal and badge order replacement heads

Revision ID: r1s2t3u4v5w6
Revises: q1r2s3t4u5v6, p2q3r4s5t6u7
Create Date: 2026-07-27 18:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'r1s2t3u4v5w6'
down_revision: Union[str, Sequence[str], None] = ('q1r2s3t4u5v6', 'p2q3r4s5t6u7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
