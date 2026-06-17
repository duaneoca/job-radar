"""add slack_connections — per-user Slack workspace install (JR-6)

Stores each user's workspace-scoped bot token (encrypted) + chosen channel, from
the "Add to Slack" OAuth v2 install. One row per user.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slack_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("encrypted_bot_token", sa.Text(), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=True),
        sa.Column("team_name", sa.String(length=255), nullable=True),
        sa.Column("bot_user_id", sa.String(length=64), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_slack_connections_user_id", "slack_connections", ["user_id"])
    op.create_index("ix_slack_connections_user_id", "slack_connections", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_slack_connections_user_id", "slack_connections")
    op.drop_constraint("uq_slack_connections_user_id", "slack_connections", type_="unique")
    op.drop_table("slack_connections")
