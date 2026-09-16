"""GET /agent/inbox — regression coverage.

Born from a staging 500: InboxInteractionOut.match_confidence was a bare
`float`, but the column and AgentInteractionIn have been nullable since June
(null = no match). One no-match interaction made the whole inbox listing
unserialisable — reported by the V2 agent, reproduced on staging
(?status=needs_review → 500 while ?status=processed → 200, because only the
needs_review page contained such a row).
"""

import uuid
from datetime import datetime, timezone

from app import models

from .conftest import TEST_USER_ID


def _email_with_interaction(db, confidence):
    email = models.InboxEmail(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        message_id=f"<{uuid.uuid4()}@test>",
        subject="Your application status",
        sender="noreply@acme.example",
        category=models.EmailCategory.APPLICATION_CONFIRMATION,
        status=models.EmailStatus.NEEDS_REVIEW,
        received_at=datetime.now(timezone.utc),
        confidence=0.9,
    )
    db.add(email)
    db.flush()
    db.add(models.InboxInteraction(
        id=uuid.uuid4(),
        inbox_email_id=email.id,
        user_id=TEST_USER_ID,
        matched_review_id=None,
        match_confidence=confidence,
    ))
    db.commit()
    return email


def test_inbox_lists_a_no_match_interaction(client, db):
    """The regression: null match_confidence must serialise, not 500."""
    _email_with_interaction(db, confidence=None)
    r = client.get("/agent/inbox", params={"status": "needs_review"})
    assert r.status_code == 200, r.text
    inter = r.json()["items"][0]["interactions"][0]
    assert inter["match_confidence"] is None
    assert inter["matched_review_id"] is None


def test_inbox_still_serialises_a_real_confidence(client, db):
    _email_with_interaction(db, confidence=0.87)
    r = client.get("/agent/inbox", params={"status": "needs_review"})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["interactions"][0]["match_confidence"] == 0.87
