"""Mailbox retention — per-user days before Social / Postings mail goes to Trash

INTEGRATION_SPEC §3.8. Two nullable ints on email_credentials; NULL = off.
Returned as the optional `retention` block in /agent/cloud/config, omitted when
both are off, so the agent behaves exactly as with an older Job Radar.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_credentials", sa.Column("retention_social_days", sa.Integer(), nullable=True))
    op.add_column("email_credentials", sa.Column("retention_postings_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_credentials", "retention_postings_days")
    op.drop_column("email_credentials", "retention_social_days")
