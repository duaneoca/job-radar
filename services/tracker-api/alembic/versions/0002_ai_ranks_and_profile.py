"""Add AI rank columns to jobs and create profiles table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-19
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add per-dimension rank columns + recommended to jobs ──
    op.add_column("jobs", sa.Column("skills_rank", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("experience_rank", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("location_rank", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("education_rank", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("salary_rank", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("recommended", sa.Boolean(), nullable=True))

    # ── Create profiles table ──
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default="1"),
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
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("profiles")

    op.drop_column("jobs", "recommended")
    op.drop_column("jobs", "salary_rank")
    op.drop_column("jobs", "education_rank")
    op.drop_column("jobs", "location_rank")
    op.drop_column("jobs", "experience_rank")
    op.drop_column("jobs", "skills_rank")
