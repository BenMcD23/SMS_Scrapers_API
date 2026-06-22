"""add logs column to scraper_runs

Revision ID: h2i3j4k5l6m7
Revises: 099f806dc621
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h2i3j4k5l6m7'
down_revision: Union[str, Sequence[str], None] = '099f806dc621'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Inspector works on both Postgres (prod) and SQLite (local dev)
    conn = op.get_bind()
    cols = [c["name"] for c in sa.inspect(conn).get_columns("Scraper_Runs")]
    if "logs" not in cols:
        op.add_column("Scraper_Runs", sa.Column("logs", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    cols = [c["name"] for c in sa.inspect(conn).get_columns("Scraper_Runs")]
    if "logs" in cols:
        op.drop_column("Scraper_Runs", "logs")
