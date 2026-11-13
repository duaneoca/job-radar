"""Agent V2 Phase B — one inbox posting per (user, dedup_key)

INTEGRATION_SPEC §3.6, Phase B. A partial unique index on
inbox_postings(user_id, dedup_key) WHERE dedup_key IS NOT NULL: the cloud
agent keeps no state between runs, so this is its cross-email duplicate
memory. Rows without a key (older agents, older rows) are unaffected.

Phase A stored keys without enforcing them, so duplicates can already exist.
They are kept — they may be imported or dismissed — but every copy except the
earliest loses its key, so the index can be built. Nothing is deleted.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE inbox_postings SET dedup_key = NULL
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY user_id, dedup_key ORDER BY created_at, id
                ) AS n
                FROM inbox_postings
                WHERE dedup_key IS NOT NULL
            ) ranked
            WHERE n > 1
        )
    """))
    op.create_index(
        "uq_inbox_postings_user_dedup_key", "inbox_postings", ["user_id", "dedup_key"],
        unique=True, postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_inbox_postings_user_dedup_key", table_name="inbox_postings")
