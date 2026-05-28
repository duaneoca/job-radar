"""add AI generation fields: research prompt, application templates, per-job answers

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Criteria — user-level AI generation settings
    op.add_column("criteria", sa.Column("research_prompt", sa.Text(), nullable=True))
    op.add_column("criteria", sa.Column("application_templates", sa.JSON(), nullable=True))

    # UserJobReview — per-job AI-generated content
    op.add_column("user_job_reviews", sa.Column("research_summary", sa.Text(), nullable=True))
    op.add_column("user_job_reviews", sa.Column("application_answers", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_job_reviews", "application_answers")
    op.drop_column("user_job_reviews", "research_summary")
    op.drop_column("criteria", "application_templates")
    op.drop_column("criteria", "research_prompt")
