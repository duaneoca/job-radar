"""add recruiters table + user_job_reviews.recruiter_id

A lightweight recruiter CRM: name/contact/employer, companies represented (JSON),
LinkedIn URL, agency-vs-in_house type, relationship status, last-contacted date,
and notes. Jobs link to the sourcing recruiter via user_job_reviews.recruiter_id
(SET NULL so deleting a recruiter only unlinks, never deletes the job).

status/type are plain VARCHARs (validated in the app), per the project's
preference for VARCHAR over native PG enums.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recruiters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("employer", sa.String(length=200), nullable=True),
        sa.Column("companies_represented", sa.JSON(), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("last_contacted", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_recruiters_user_id", "recruiters", ["user_id"])
    op.create_index("ix_recruiters_email", "recruiters", ["email"])

    op.add_column(
        "user_job_reviews",
        sa.Column("recruiter_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_job_reviews_recruiter_id",
        "user_job_reviews", "recruiters",
        ["recruiter_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_user_job_reviews_recruiter_id", "user_job_reviews", ["recruiter_id"])


def downgrade() -> None:
    op.drop_index("ix_user_job_reviews_recruiter_id", "user_job_reviews")
    op.drop_constraint("fk_user_job_reviews_recruiter_id", "user_job_reviews", type_="foreignkey")
    op.drop_column("user_job_reviews", "recruiter_id")
    op.drop_index("ix_recruiters_email", "recruiters")
    op.drop_index("ix_recruiters_user_id", "recruiters")
    op.drop_table("recruiters")
