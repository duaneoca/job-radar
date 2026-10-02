"""Clear unusable_output verdicts and counts left behind by the streak bug

Until v1.14.0 the worker reported a successful score only when a verdict was
already recorded, so nothing reset user_api_keys.unusable_streak between bad
answers: "three in a row" was really "three ever". A model that misformatted one
review in a few hundred eventually got an unusable_output verdict, and that kind
BLOCKS scoring until the user changes model — no success can ever clear it,
because the worker stops calling.

The fix stops new false verdicts but cannot tell the old ones apart from real
ones. So this clears them all, and lets real ones come back: a model that truly
won't answer in the format is re-flagged after three consecutive bad answers.

  * verdicts recorded before the fix — unusable_output with no last_error_model
    (0027 added that column; every verdict written before it is NULL) — are
    cleared, with their count;
  * counts on keys with no verdict are reset, since they were accumulated the
    same way and would otherwise re-raise the warning on a single bad answer.

Verdicts recorded after the fix carry a model and are left alone, as is every
other kind (a rejected key or retired model is still true).

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-02
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE user_api_keys
           SET last_error_kind = NULL,
               last_error = NULL,
               last_error_at = NULL,
               unusable_streak = 0
         WHERE last_error_kind = 'unusable_output'
           AND last_error_model IS NULL
        """
    )
    op.execute(
        """
        UPDATE user_api_keys
           SET unusable_streak = 0
         WHERE last_error_kind IS NULL
           AND unusable_streak > 0
        """
    )


def downgrade() -> None:
    # Cleared verdicts were false positives; there is nothing worth restoring.
    pass
