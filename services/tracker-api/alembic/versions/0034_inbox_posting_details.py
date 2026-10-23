"""inbox_postings: location, remote, annual salary, description

Recruiter postings carry the details Add Job pre-fills (agent spec §3.5). The
description is email body text — the one deliberate exception to content
minimization, by the user's decision on 2026-10-23.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_COLS = [
    ("location", sa.Text()),
    ("remote", sa.Boolean()),
    ("salary_min", sa.Integer()),
    ("salary_max", sa.Integer()),
    ("description", sa.Text()),
]


def upgrade() -> None:
    for name, type_ in _COLS:
        op.add_column("inbox_postings", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("inbox_postings", name)
