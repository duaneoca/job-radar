"""add commute/work-style fields to criteria

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("criteria", sa.Column("search_locations", sa.JSON(), nullable=True))
    op.add_column("criteria", sa.Column("work_style", sa.String(20), nullable=True, server_default="any"))
    op.add_column("criteria", sa.Column("home_city", sa.String(255), nullable=True))
    op.add_column("criteria", sa.Column("max_commute_miles", sa.Integer(), nullable=True))
    op.add_column("criteria", sa.Column("target_companies", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("criteria", "target_companies")
    op.drop_column("criteria", "max_commute_miles")
    op.drop_column("criteria", "home_city")
    op.drop_column("criteria", "work_style")
    op.drop_column("criteria", "search_locations")
