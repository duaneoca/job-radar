"""jobs.match_key — recognise a re-listed job

Aggregators re-list one opening many times (Adzuna re-posts, JSearch's unstable
ids, the same posting via several boards); production had 2,847 extra copies for
one user, each AI-scored at their expense. match_key = company|title|city with
everything but [a-z0-9] removed — see app/dedup.py, whose match_key() this
backfill must equal (checked against Postgres when written).

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

# The SQL twin of dedup.match_key. NULL when company or title normalise to
# nothing, like the Python.
BACKFILL = """
UPDATE jobs SET match_key = left(
    regexp_replace(lower(company), '[^a-z0-9]', '', 'g') || '|' ||
    regexp_replace(lower(title),   '[^a-z0-9]', '', 'g') || '|' ||
    regexp_replace(lower(split_part(coalesce(location, ''), ',', 1)), '[^a-z0-9]', '', 'g'),
    255)
 WHERE regexp_replace(lower(coalesce(company, '')), '[^a-z0-9]', '', 'g') <> ''
   AND regexp_replace(lower(coalesce(title, '')),   '[^a-z0-9]', '', 'g') <> ''
"""


def upgrade() -> None:
    op.add_column("jobs", sa.Column("match_key", sa.String(255), nullable=True))
    op.create_index("ix_jobs_match_key", "jobs", ["match_key"])
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_index("ix_jobs_match_key", table_name="jobs")
    op.drop_column("jobs", "match_key")
