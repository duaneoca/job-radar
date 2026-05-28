"""Multi-user schema: users, per-user reviews, API keys, LinkedIn connections

Drops and recreates all tables from scratch.
All previous test data is intentionally discarded.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-19
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Drop everything from the old single-user schema ──────
    op.drop_table("timeline_events")
    op.drop_table("criteria")
    op.drop_table("profiles")
    op.drop_table("jobs")

    # ── Users ────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_approved", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # ── Shared job pool ───────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.Column("date_posted", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_scraped", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Per-user job reviews ──────────────────────────────────
    op.create_table(
        "user_job_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_pros", sa.JSON(), nullable=True),
        sa.Column("ai_cons", sa.JSON(), nullable=True),
        sa.Column("skills_rank", sa.Integer(), nullable=True),
        sa.Column("experience_rank", sa.Integer(), nullable=True),
        sa.Column("location_rank", sa.Integer(), nullable=True),
        sa.Column("education_rank", sa.Integer(), nullable=True),
        sa.Column("salary_rank", sa.Integer(), nullable=True),
        sa.Column("recommended", sa.Boolean(), nullable=True),
        sa.Column("ai_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="new"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("date_applied", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_contact", sa.Boolean(), server_default="false"),
        sa.Column("contact_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"],  ["jobs.id"],  ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_job"),
    )
    op.create_index("ix_user_job_reviews_user_id", "user_job_reviews", ["user_id"])
    op.create_index("ix_user_job_reviews_job_id",  "user_job_reviews", ["job_id"])

    # ── Timeline events ───────────────────────────────────────
    op.create_table(
        "timeline_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["review_id"], ["user_job_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Per-user criteria ─────────────────────────────────────
    op.create_table(
        "criteria",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("job_titles", sa.JSON(), nullable=True),
        sa.Column("required_skills", sa.JSON(), nullable=True),
        sa.Column("preferred_skills", sa.JSON(), nullable=True),
        sa.Column("excluded_companies", sa.JSON(), nullable=True),
        sa.Column("locations", sa.JSON(), nullable=True),
        sa.Column("remote_only", sa.Boolean(), server_default="false"),
        sa.Column("min_salary", sa.Integer(), nullable=True),
        sa.Column("extra_instructions", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_criteria_user_id", "criteria", ["user_id"])

    # ── Per-user profiles ─────────────────────────────────────
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("education", sa.Text(), nullable=True),
        sa.Column("desired_salary", sa.Integer(), nullable=True),
        sa.Column("commute_preference", sa.String(255), nullable=True),
        sa.Column("extra_context", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    # ── Encrypted API keys ────────────────────────────────────
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )
    op.create_index("ix_user_api_keys_user_id", "user_api_keys", ["user_id"])

    # ── LinkedIn connections ──────────────────────────────────
    op.create_table(
        "linkedin_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("position", sa.String(255), nullable=True),
        sa.Column("connected_on", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_linkedin_connections_user_id",  "linkedin_connections", ["user_id"])
    op.create_index("ix_linkedin_connections_company",   "linkedin_connections", ["company"])


def downgrade() -> None:
    op.drop_table("linkedin_connections")
    op.drop_table("user_api_keys")
    op.drop_table("profiles")
    op.drop_table("criteria")
    op.drop_table("timeline_events")
    op.drop_table("user_job_reviews")
    op.drop_table("jobs")
    op.drop_table("users")
