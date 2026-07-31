"""jobs.board_missing_count — consecutive company-board scrapes missing a posting

Company boards return EVERY open role, so a posting that stops appearing has been
taken down. (An aggregator search returns a ranked, truncated slice, where absence
means nothing.) Two consecutive misses expire the job, so one bad scrape cannot
clear a company.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("board_missing_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "board_missing_count")
