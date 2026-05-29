"""Add scoring_prompt to criteria

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("criteria", sa.Column("scoring_prompt", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("criteria", "scoring_prompt")
