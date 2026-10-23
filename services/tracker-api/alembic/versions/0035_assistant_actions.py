"""assistant_actions — what connected AI assistants did, for daily limits

One row per research / tailoring run started, or job expired or dismissed, by a
connected app (app/assistant.py). The user's own clicks are never logged.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grant_id", sa.Uuid(), sa.ForeignKey("oauth_grants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vendor", sa.String(20), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_actions_user_action_time", "assistant_actions",
                    ["user_id", "action", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_assistant_actions_user_action_time", table_name="assistant_actions")
    op.drop_table("assistant_actions")
