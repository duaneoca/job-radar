"""OAuth tables — AI assistants (Claude, ChatGPT, Gemini, Grok) connect as the user

Job Radar is the authorization server for its own MCP connector: clients
(DCR or Client ID Metadata Document, admitted only by vendor-allowlisted
redirect URIs), single-use PKCE authorization codes, one grant per consent
(the row behind Settings → Connected apps), and hashed access/refresh tokens
with refresh rotation. See app/oauth.py.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(512), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("vendor", sa.String(20), nullable=False),
        sa.Column("client_name", sa.String(100), nullable=True),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(512), sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_oauth_authorization_codes_user_id", "oauth_authorization_codes", ["user_id"])
    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(512), sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_oauth_grants_user_id", "oauth_grants", ["user_id"])
    op.create_index("ix_oauth_grants_client_id", "oauth_grants", ["client_id"])
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("grant_id", sa.Uuid(), sa.ForeignKey("oauth_grants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_oauth_tokens_grant_id", "oauth_tokens", ["grant_id"])
    op.create_index("ix_oauth_tokens_token_hash", "oauth_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_grants")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
