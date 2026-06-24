"""POST /agent/interactions — including the no-match case where the agent sends
matched_review_id=null AND match_confidence=null together (spec v-current §1.3/§3.2)."""

from app import models
from app.config import settings

from .conftest import TEST_USER_ID

TOKEN = "internal-token-xyz"


def _headers(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    return {"X-Internal-Token": TOKEN, "X-User-Id": str(TEST_USER_ID)}


def _payload(**over):
    base = {
        "message_id": "m-1", "subject": "We received your application",
        "sender": "noreply@acme.com", "received_at": "2026-06-24T00:00:00Z",
        "category": "application_confirmation", "confidence": 0.9,
    }
    base.update(over)
    return base


def test_no_match_nulls_accepted(client, test_user, db, monkeypatch):
    """matched_review_id + match_confidence both null → 201, stored null, needs_review."""
    r = client.post("/agent/interactions",
                    json=_payload(matched_review_id=None, match_confidence=None),
                    headers=_headers(monkeypatch))
    assert r.status_code == 201, r.text

    inter = db.query(models.InboxInteraction).filter_by(user_id=TEST_USER_ID).first()
    assert inter is not None and inter.match_confidence is None
    email = db.query(models.InboxEmail).filter_by(user_id=TEST_USER_ID).first()
    assert email.status == models.EmailStatus.NEEDS_REVIEW


def test_match_confidence_may_be_omitted(client, test_user, db, monkeypatch):
    """Absent match_confidence is fine too (defaults to null)."""
    r = client.post("/agent/interactions", json=_payload(message_id="m-2"),
                    headers=_headers(monkeypatch))
    assert r.status_code == 201, r.text
