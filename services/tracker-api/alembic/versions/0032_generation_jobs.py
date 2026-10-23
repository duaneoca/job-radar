"""generation_jobs — research and résumé tailoring run in the background

A research summary with Tavily context or a tailor pass takes 30–75 s, which
sat the user on a spinner and ran into nginx's 95 s / Cloudflare's 100 s
ceilings. Each request is now a row here; the work runs after the response and
the page (or an MCP client) polls the row. See app/generation_jobs.py.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("user_job_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="web"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_generation_jobs_user_id", "generation_jobs", ["user_id"])
    op.create_index("ix_generation_jobs_review_id", "generation_jobs", ["review_id"])
    # The two hot lookups: "is anything of this kind running for this user?" and
    # "the latest run of each kind for this review" — both read newest-first.
    op.create_index("ix_generation_jobs_user_kind_status", "generation_jobs", ["user_id", "kind", "status"])
    # One active run per user per kind, decided by the database (see the model).
    op.create_index("uq_generation_jobs_active", "generation_jobs", ["user_id", "kind"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index("uq_generation_jobs_active", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_user_kind_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_review_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_user_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
