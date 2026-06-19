"""add profiles.resume_structured + resume_structured_stale (résumé tailoring Phase 1)

Structured parse of resume_text (sections JSON) plus a freshness flag set True
whenever resume_text is edited, so the structured copy is re-parsed lazily.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("resume_structured", sa.JSON(), nullable=True))
    op.add_column(
        "profiles",
        sa.Column("resume_structured_stale", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("profiles", "resume_structured_stale")
    op.drop_column("profiles", "resume_structured")
