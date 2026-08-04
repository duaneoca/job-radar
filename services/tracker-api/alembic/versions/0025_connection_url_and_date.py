"""linkedin_connections.profile_url + connected_at

Two columns the CSV always had and we were throwing away.

`profile_url` is the export's "URL" column — the public LinkedIn profile. The
importer never looked for it, so the table had no way to link back to a person.

`connected_at` is `connected_on` parsed into a real date. The raw string sorts
wrong ("07 Apr 2026" < "08 Feb 2019"), which matters now that the column is
sortable in the UI. The string is kept and still displayed; this is purely for
ordering.

The backfill parses existing rows so nobody has to re-import to get sorting.
Links are a different story: the URL was never stored, so `profile_url` stays
NULL until the user uploads their CSV again. Nothing here can invent it.

The parse runs in Python, deliberately, using the same formats as the importer.
Postgres to_date() is the obvious alternative and the wrong one: given a string
that doesn't match the format it either raises (aborting the migration) or
silently coerces to a nonsense date, and "try each format in turn" needs a
parser that fails cleanly.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-04
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# Keep in step with _DATE_FORMATS in routers/connections.py.
_FORMATS = ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y", "%b %d, %Y")

_BATCH = 1000


def _parse(raw):
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def upgrade() -> None:
    op.add_column("linkedin_connections", sa.Column("connected_at", sa.Date(), nullable=True))
    op.add_column("linkedin_connections", sa.Column("profile_url", sa.String(500), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, connected_on FROM linkedin_connections WHERE connected_on IS NOT NULL"
    )).fetchall()

    updates = [{"id": r[0], "d": _parse(r[1])} for r in rows]
    updates = [u for u in updates if u["d"] is not None]
    for i in range(0, len(updates), _BATCH):
        conn.execute(
            sa.text("UPDATE linkedin_connections SET connected_at = :d WHERE id = :id"),
            updates[i:i + _BATCH],
        )


def downgrade() -> None:
    op.drop_column("linkedin_connections", "profile_url")
    op.drop_column("linkedin_connections", "connected_at")
