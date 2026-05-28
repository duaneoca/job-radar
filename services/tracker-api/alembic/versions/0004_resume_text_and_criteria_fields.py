"""add resume_text to profiles and nice_to_have_skills/years_experience to criteria

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # profiles: add resume_text
    op.add_column("profiles", sa.Column("resume_text", sa.Text(), nullable=True))

    # criteria: add nice_to_have_skills and years_experience
    op.add_column("criteria", sa.Column("nice_to_have_skills", sa.JSON(), nullable=True))
    op.add_column("criteria", sa.Column("years_experience", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "resume_text")
    op.drop_column("criteria", "nice_to_have_skills")
    op.drop_column("criteria", "years_experience")
