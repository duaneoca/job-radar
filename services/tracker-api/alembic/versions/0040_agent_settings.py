"""agent_settings — per-user email agent settings for local and cloud agents

GET /agent/settings (INTEGRATION_SPEC §2.4) serves one settings document to
local and cloud agents alike. The email policy and retention lived on
email_credentials, which a local agent (mail login in its own .env) may not
have — so they move here, and every existing value is copied across. The old
columns are left in place, unread, and get dropped in a later migration.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-25
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

# Copied from email_credentials; the column names are the same on both tables.
_TYPES = {
    "allowed_sender_domains": sa.JSON(),
    "require_sender_auth": sa.Boolean(),
    "zero_postings_action": sa.String(20),
    "trusted_authserv_ids": sa.JSON(),
    "retention_social_days": sa.Integer(),
    "retention_postings_days": sa.Integer(),
}
_COPIED = tuple(_TYPES)


def upgrade() -> None:
    op.create_table(
        "agent_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sorter_enabled", sa.Boolean(), nullable=True),
        sa.Column("sorter_min_confidence", sa.Float(), nullable=True),
        sa.Column("sorter_min_margin", sa.Float(), nullable=True),
        sa.Column("bulk_recruiter_domains", sa.JSON(), nullable=True),
        sa.Column("allowed_sender_domains", sa.JSON(), nullable=True),
        sa.Column("require_sender_auth", sa.Boolean(), nullable=True),
        sa.Column("zero_postings_action", sa.String(20), nullable=True),
        sa.Column("trusted_authserv_ids", sa.JSON(), nullable=True),
        sa.Column("dedup_window_days", sa.Integer(), nullable=True),
        sa.Column("max_email_age_days", sa.Integer(), nullable=True),
        sa.Column("retention_social_days", sa.Integer(), nullable=True),
        sa.Column("retention_postings_days", sa.Integer(), nullable=True),
        sa.Column("staffing_enabled", sa.Boolean(), nullable=True),
        sa.Column("staffing_auto_draft", sa.Boolean(), nullable=True),
        sa.Column("staffing_excluded_sender_domains", sa.JSON(), nullable=True),
        sa.Column("staffing_allowed_employment_types", sa.JSON(), nullable=True),
        sa.Column("staffing_draft_score_threshold", sa.Float(), nullable=True),
        sa.Column("staffing_dedup_window_days", sa.Integer(), nullable=True),
        sa.Column("staffing_mark_read", sa.JSON(), nullable=True),
        sa.Column("staffing_draft_prompt", sa.Text(), nullable=True),
        sa.Column("staffing_slack_notify", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_settings_user_id", "agent_settings", ["user_id"], unique=True)

    _copy_from_email_credentials(op.get_bind())


def _copy_from_email_credentials(bind) -> None:
    """One agent_settings row per user who has set any of the copied fields.
    Plain SQLAlchemy rather than Postgres SQL, so tests can run it on SQLite."""
    now = datetime.now(timezone.utc)
    cols = list(_COPIED)
    src = sa.table("email_credentials", sa.column("user_id", sa.Uuid()),
                   *(sa.column(c, _TYPES[c]) for c in cols))
    dst = sa.table("agent_settings", sa.column("id", sa.Uuid()), sa.column("user_id", sa.Uuid()),
                   sa.column("created_at", sa.DateTime(timezone=True)),
                   sa.column("updated_at", sa.DateTime(timezone=True)),
                   *(sa.column(c, _TYPES[c]) for c in cols))
    rows = []
    for r in bind.execute(sa.select(src)).mappings():
        values = {c: r[c] for c in cols}
        if all(v is None for v in values.values()):
            continue
        rows.append({"id": uuid.uuid4(), "user_id": r["user_id"],
                     "created_at": now, "updated_at": now, **values})
    if rows:
        bind.execute(dst.insert(), rows)


def downgrade() -> None:
    op.drop_index("ix_agent_settings_user_id", table_name="agent_settings")
    op.drop_table("agent_settings")
