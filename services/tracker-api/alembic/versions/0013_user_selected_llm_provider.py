"""add users.selected_llm_provider — explicit active LLM key

Lets a user pick which stored LLM key is active (radio button in Settings → API
Keys) instead of the implicit "priority order / most-recently-updated" rules.
NULL = fall back to priority order. Stored as VARCHAR(50) holding the LLMProvider
name, matching the existing user_api_keys.provider column.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("selected_llm_provider", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "selected_llm_provider")
