"""add scraper job queue

Adds the Scraper_Jobs / Scraper_Job_Logs tables the home worker claims work
from. The partial unique index is the lock that stops two runs of the same
named scraper — it replaces the old in-process guard, which only knew about
runs inside its own container.

Revision ID: a4b5c6d7e8f9
Revises: z2a3b4c5d6e7
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, Sequence[str], None] = 'z2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACTIVE_WHERE = (
    "status IN ('queued','claimed','running') "
    "AND scraper_id <> 'upload-qualifications'"
)


def upgrade() -> None:
    op.create_table(
        "Scraper_Jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scraper_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scraper_jobs_status", "Scraper_Jobs", ["status"])
    op.create_index(
        "uq_scraper_jobs_active",
        "Scraper_Jobs",
        ["scraper_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_WHERE),
        sqlite_where=sa.text(_ACTIVE_WHERE),
    )

    op.create_table(
        "Scraper_Job_Logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False, server_default="info"),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["job_id"], ["Scraper_Jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "seq", name="uq_scraper_job_logs_seq"),
    )


def downgrade() -> None:
    op.drop_table("Scraper_Job_Logs")
    op.drop_index("uq_scraper_jobs_active", table_name="Scraper_Jobs")
    op.drop_index("ix_scraper_jobs_status", table_name="Scraper_Jobs")
    op.drop_table("Scraper_Jobs")
