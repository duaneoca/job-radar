"""add enabled flag to email_credentials (JR-5)

Per-user pause for the cloud agent: the global CronJob iterates users who have
credentials AND are enabled. Existing rows default to enabled.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_credentials",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("email_credentials", "enabled")
