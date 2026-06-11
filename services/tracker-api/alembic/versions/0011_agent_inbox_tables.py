"""add email agent inbox tables (JR-1)

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── new enum types ────────────────────────────────────────
    op.execute("CREATE TYPE IF NOT EXISTS emailcategory AS ENUM ('recruiter_outreach','application_confirmation','job_alert','network_notification')")
    op.execute("CREATE TYPE IF NOT EXISTS emailstatus AS ENUM ('pending','processed','needs_review','discarded')")
    op.execute("CREATE TYPE IF NOT EXISTS importstatus AS ENUM ('pending','imported','dismissed')")
    op.execute("CREATE TYPE IF NOT EXISTS emailprovider AS ENUM ('gmail','imap')")
    op.execute("CREATE TYPE IF NOT EXISTS hitlstatus AS ENUM ('pending','resolved','abandoned')")
    op.execute("CREATE TYPE IF NOT EXISTS agentrunstatus AS ENUM ('success','partial','failed')")
    op.execute("CREATE TYPE IF NOT EXISTS agentenvironment AS ENUM ('local','cloud')")

    # ── inbox_emails ──────────────────────────────────────────
    op.create_table(
        "inbox_emails",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.Enum("recruiter_outreach", "application_confirmation", "job_alert", "network_notification", name="emailcategory", create_type=False), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("raw_extracted_json", sa.JSON(), nullable=True),
        sa.Column("validation_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("pending", "processed", "needs_review", "discarded", name="emailstatus", create_type=False), nullable=False, server_default="pending"),
        sa.Column("langfuse_trace_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "message_id", name="uq_inbox_user_message"),
    )
    op.create_index("ix_inbox_emails_user_id", "inbox_emails", ["user_id"])

    # ── inbox_postings ────────────────────────────────────────
    op.create_table(
        "inbox_postings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("inbox_email_id", sa.Uuid(), sa.ForeignKey("inbox_emails.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("action_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("possible_duplicate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("matched_review_id", sa.Uuid(), sa.ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("import_status", sa.Enum("pending", "imported", "dismissed", name="importstatus", create_type=False), nullable=False, server_default="pending"),
        sa.Column("imported_review_id", sa.Uuid(), sa.ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_inbox_postings_inbox_email_id", "inbox_postings", ["inbox_email_id"])
    op.create_index("ix_inbox_postings_user_id", "inbox_postings", ["user_id"])

    # ── inbox_interactions ────────────────────────────────────
    op.create_table(
        "inbox_interactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("inbox_email_id", sa.Uuid(), sa.ForeignKey("inbox_emails.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched_review_id", sa.Uuid(), sa.ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("previous_status", sa.Enum("new", "reviewed", "applied", "dismissed", "interviewing", "offer", "rejected", "expired", name="jobstatus", create_type=False), nullable=True),
        sa.Column("new_status", sa.Enum("new", "reviewed", "applied", "dismissed", "interviewing", "offer", "rejected", "expired", name="jobstatus", create_type=False), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_inbox_interactions_inbox_email_id", "inbox_interactions", ["inbox_email_id"])
    op.create_index("ix_inbox_interactions_user_id", "inbox_interactions", ["user_id"])

    # ── agent_api_keys ────────────────────────────────────────
    op.create_table(
        "agent_api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("key_hint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_agent_api_keys_user_id", "agent_api_keys", ["user_id"])
    op.create_index("ix_agent_api_keys_key_hash", "agent_api_keys", ["key_hash"])

    # ── email_credentials ─────────────────────────────────────
    op.create_table(
        "email_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("provider", sa.Enum("gmail", "imap", name="emailprovider", create_type=False), nullable=False),
        sa.Column("encrypted_blob", sa.Text(), nullable=False),
        sa.Column("folder_root", sa.Text(), nullable=True),
        sa.Column("folder_interaction", sa.Text(), nullable=True),
        sa.Column("folder_postings", sa.Text(), nullable=True),
        sa.Column("folder_social", sa.Text(), nullable=True),
        sa.Column("folder_unprocessed", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_email_credentials_user_id", "email_credentials", ["user_id"])

    # ── hitl_decisions ────────────────────────────────────────
    op.create_table(
        "hitl_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hitl_id", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Enum("pending", "resolved", "abandoned", name="hitlstatus", create_type=False), nullable=False, server_default="pending"),
        sa.Column("choice_review_id", sa.Uuid(), sa.ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_hitl_decisions_user_id", "hitl_decisions", ["user_id"])
    op.create_index("ix_hitl_decisions_hitl_id", "hitl_decisions", ["hitl_id"])

    # ── agent_runs ────────────────────────────────────────────
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment", sa.Enum("local", "cloud", name="agentenvironment", create_type=False), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("success", "partial", "failed", name="agentrunstatus", create_type=False), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emails_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interactions_recorded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("hitl_decisions")
    op.drop_table("email_credentials")
    op.drop_table("agent_api_keys")
    op.drop_table("inbox_interactions")
    op.drop_table("inbox_postings")
    op.drop_table("inbox_emails")

    op.execute("DROP TYPE IF EXISTS agentenvironment")
    op.execute("DROP TYPE IF EXISTS agentrunstatus")
    op.execute("DROP TYPE IF EXISTS hitlstatus")
    op.execute("DROP TYPE IF EXISTS emailprovider")
    op.execute("DROP TYPE IF EXISTS importstatus")
    op.execute("DROP TYPE IF EXISTS emailstatus")
    op.execute("DROP TYPE IF EXISTS emailcategory")
