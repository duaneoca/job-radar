"""agent_runs.late — a run record that was spooled and delivered after the fact

When the agent can't reach Job Radar it keeps its run records and flushes them
later (job-radar-agent docs/run-record-spool.md). Such a record is accurate
about when the run happened, just late; marking it lets the dashboard show the
outage as a period instead of a silence that looks like "no mail".

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("agent_runs", "late")
