"""make inbox_interactions.match_confidence nullable (no-match interactions)

The agent sends matched_review_id=null + match_confidence=null together for a
no-match interaction (e.g. an application_confirmation with no tracked job →
needs_review). The column was NOT NULL; null is now a valid "no match" value.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("inbox_interactions", "match_confidence",
                    existing_type=sa.Float(), nullable=True, server_default=None)


def downgrade() -> None:
    # Backfill nulls before restoring NOT NULL so existing no-match rows don't block it.
    op.execute("UPDATE inbox_interactions SET match_confidence = 0.0 WHERE match_confidence IS NULL")
    op.alter_column("inbox_interactions", "match_confidence",
                    existing_type=sa.Float(), nullable=False)
