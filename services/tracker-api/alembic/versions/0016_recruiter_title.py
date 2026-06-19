"""add recruiters.title

The email agent now extracts a recruiter's title (e.g. "Senior Technical
Recruiter") from the signature; give the CRM a column for it instead of folding
it into notes.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recruiters", sa.Column("title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("recruiters", "title")
