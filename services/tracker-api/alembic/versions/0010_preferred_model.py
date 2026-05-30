"""add preferred_model to user_api_keys

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_api_keys",
        sa.Column("preferred_model", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_api_keys", "preferred_model")
