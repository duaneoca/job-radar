"""add voice_guidelines to criteria

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("criteria", sa.Column("voice_guidelines", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("criteria", "voice_guidelines")
