"""Interview prep: career_stories, interview_prep_prompt, interview_questions

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    # Career stories stored on the user's profile
    op.add_column("profiles", sa.Column("career_stories", sa.JSON(), nullable=True))

    # Editable interview prep prompt on criteria
    op.add_column("criteria", sa.Column("interview_prep_prompt", sa.Text(), nullable=True))

    # Generated interview question cards stored on the review
    op.add_column("user_job_reviews", sa.Column("interview_questions", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("user_job_reviews", "interview_questions")
    op.drop_column("criteria", "interview_prep_prompt")
    op.drop_column("profiles", "career_stories")
