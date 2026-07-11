"""app_settings key/value table for global admin-controlled feature flags

First consumer: email_agent_enabled (defaults to False when the row is absent,
so the email-agent feature is hidden/disabled until an admin turns it on).

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
