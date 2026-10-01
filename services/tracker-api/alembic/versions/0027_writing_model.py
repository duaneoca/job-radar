"""user_api_keys.writing_model + last_error_model — separate model for employer-facing text

Users wanted a cheap model for work only they read (scoring, research) and a
better one for anything an employer may read (application answers, tailored
résumés, interview prep). writing_model is that second choice; NULL means "same
as the analysis model" (preferred_model), so every existing key behaves exactly
as before.

last_error_model records which model a model-scoped verdict (invalid_model,
unusable_output, rate_limited) is about. With two models on one key, a retired
writing model must not block scoring, and a scoring success must not erase a
real writing failure. NULL — every existing row — means "applies to the whole
key", which is what those rows meant when they were written.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_api_keys", sa.Column("writing_model", sa.String(100), nullable=True))
    op.add_column("user_api_keys", sa.Column("last_error_model", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("user_api_keys", "last_error_model")
    op.drop_column("user_api_keys", "writing_model")
