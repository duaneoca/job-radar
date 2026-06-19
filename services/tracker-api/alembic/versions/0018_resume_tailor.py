"""add user_job_reviews.resume_tailor + criteria.resume_tailor_prompt (tailoring Phase 2)

Per-job tailored-résumé state (original/tailored/changes/status) and the editable
style prompt. The honesty core is server-side, not stored here.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_job_reviews", sa.Column("resume_tailor", sa.JSON(), nullable=True))
    op.add_column("criteria", sa.Column("resume_tailor_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("criteria", "resume_tailor_prompt")
    op.drop_column("user_job_reviews", "resume_tailor")
