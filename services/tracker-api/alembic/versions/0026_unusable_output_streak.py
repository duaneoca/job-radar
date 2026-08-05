"""user_api_keys.unusable_streak — consecutive unparseable model responses

A model that narrates its reasoning instead of returning the required JSON
produces a failure nothing recorded: the review was dropped, the job stayed
unscored, and the same job was retried forever. One user had 4,928 of 4,928 jobs
unscored with no indication why.

Counting consecutive failures rather than reacting to the first is the same rule
used for rate limits: one bad answer proves nothing, a streak is a model that
won't comply. At models.UNUSABLE_OUTPUT_STREAK the key gets
last_error_kind='unusable_output', which the UI surfaces and the worker treats
as a reason to stop calling.

NOT NULL with a server default of 0, so existing rows start clean and the column
never has to be null-checked.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_api_keys",
        sa.Column("unusable_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("user_api_keys", "unusable_streak")
