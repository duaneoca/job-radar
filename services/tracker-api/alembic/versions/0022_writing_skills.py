"""criteria.writing_skills — user-loadable writing style skills

A list of [{id, name, content, enabled, scopes}]. Each skill's content is appended
to the prompts named in its scopes (application / research / interview_prep /
resume / scoring), always below the locked honesty contract and output-format
specs so it can never override them.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("criteria", sa.Column("writing_skills", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("criteria", "writing_skills")
