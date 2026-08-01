"""user_api_keys.last_error_* — remember a permanent provider rejection

A user whose selected model gets retired sees nothing: background scoring fails
silently and the account looks healthy. Recording the verdict on the key lets the
UI say so. Only PERMANENT failures are stored — a rate limit is transient and
writing it here would tell a throttled user their model is gone.

No backfill: NULL means "no known problem", not "verified working".

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_api_keys", sa.Column("last_error_kind", sa.String(32), nullable=True))
    op.add_column("user_api_keys", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "user_api_keys",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_api_keys", "last_error_at")
    op.drop_column("user_api_keys", "last_error")
    op.drop_column("user_api_keys", "last_error_kind")
