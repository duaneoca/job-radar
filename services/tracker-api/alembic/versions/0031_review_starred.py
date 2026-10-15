"""user_job_reviews.starred — jobs the user wants to come back to

A user's own marker, separate from the AI's `recommended`. Starred reviews are
also kept out of the guess-based expiry rules and never deleted by cleanup
(app/routers/admin.py).

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_job_reviews",
        sa.Column("starred", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("user_job_reviews", "starred")
