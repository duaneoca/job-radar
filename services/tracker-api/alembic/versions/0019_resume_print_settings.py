"""add print/format settings (tailoring Phase 4 knobs)

profiles.resume_template_settings = the user's default print knobs (template, font,
density, margin, accent). user_job_reviews.resume_print_settings = optional per-résumé
override (same shape + forceBreakBefore[]), falling back to the profile default.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("resume_template_settings", sa.JSON(), nullable=True))
    op.add_column("user_job_reviews", sa.Column("resume_print_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_job_reviews", "resume_print_settings")
    op.drop_column("profiles", "resume_template_settings")
