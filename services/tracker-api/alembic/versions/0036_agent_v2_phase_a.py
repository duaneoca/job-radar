"""Agent V2 Phase A — posting dedup_key and the per-user email policy

INTEGRATION_SPEC §3.6, Phase A. Nothing here is enforced yet:

* inbox_postings.dedup_key — the agent's stable identity for a posting
  (`linkedin:<id>`, `url:<host+path>`, `ct:<company>|<title>`, …). Nullable:
  older rows and older agents have none. Phase B adds the partial unique index
  on (user_id, dedup_key); until then this column only records the key.
* email_credentials.{allowed_sender_domains, require_sender_auth,
  zero_postings_action} — the cloud agent's email policy, returned as
  `email_policy` in /agent/cloud/config. All nullable, and NULL means "not
  set": the field is omitted and the agent uses its own default. That keeps
  NULL distinct from `[]` for the domain list, which means "allow any sender".

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inbox_postings", sa.Column("dedup_key", sa.String(300), nullable=True))
    op.add_column("email_credentials", sa.Column("allowed_sender_domains", sa.JSON(), nullable=True))
    op.add_column("email_credentials", sa.Column("require_sender_auth", sa.Boolean(), nullable=True))
    op.add_column("email_credentials", sa.Column("zero_postings_action", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("email_credentials", "zero_postings_action")
    op.drop_column("email_credentials", "require_sender_auth")
    op.drop_column("email_credentials", "allowed_sender_domains")
    op.drop_column("inbox_postings", "dedup_key")
