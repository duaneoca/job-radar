"""Re-parse résumés that were parsed before custom sections existed

Until now the parsed résumé had five typed slots and nowhere else, and unknown
fields were discarded without an error — so a Certifications, Volunteering or
"Current stuff" section vanished from every tailored PDF, and section headings
were replaced by standard labels. Those parses can't be repaired in place: the
dropped content is only in the résumé text.

Marking them stale makes the next tailor re-parse once (on the user's analysis
model, as any stale résumé is). Identified by the absence of the
`custom_sections` key, which every parse now writes.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-07
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE profiles
           SET resume_structured_stale = true
         WHERE resume_structured IS NOT NULL
           AND NOT (resume_structured::jsonb ? 'custom_sections')
        """
    )


def downgrade() -> None:
    # Re-parsing is harmless; there is nothing to undo.
    pass
