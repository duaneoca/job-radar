"""Initial schema — jobs, timeline_events, criteria

Revision ID: 0001
Revises:
Create Date: 2026-04-08
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("remote", sa.Boolean(), server_default="false"),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(10), server_default="USD"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="new"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("date_posted", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_scraped", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("date_applied", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_pros", sa.JSON(), nullable=True),
        sa.Column("ai_cons", sa.JSON(), nullable=True),
        sa.Column("ai_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("external_id", "source", name="uq_jobs_external_source"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_company", "jobs", ["company"])
    op.create_index("ix_jobs_date_scraped", "jobs", ["date_scraped"])

    op.create_table(
        "timeline_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id", sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_timeline_job_id", "timeline_events", ["job_id"])

    op.create_table(
        "criteria",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("job_titles", sa.JSON(), nullable=True),
        sa.Column("required_skills", sa.JSON(), nullable=True),
        sa.Column("preferred_skills", sa.JSON(), nullable=True),
        sa.Column("excluded_companies", sa.JSON(), nullable=True),
        sa.Column("locations", sa.JSON(), nullable=True),
        sa.Column("remote_only", sa.Boolean(), server_default="false"),
        sa.Column("min_salary", sa.Integer(), nullable=True),
        sa.Column("extra_instructions", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("criteria")
    op.drop_table("timeline_events")
    op.drop_table("jobs")
