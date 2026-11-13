"""The recruiter card on an inbox row (sorter mail, INTEGRATION_SPEC §3.7).

The sorter writes recruiter outreach with no postings and a typed `recruiter`
card. The row must show who wrote, sanitised by the same rules as the
Recruiters page — the card is agent-derived (C2)."""

import uuid
from datetime import datetime, timezone

from .conftest import TEST_USER_ID

AGENT = {"X-User-Id": str(TEST_USER_ID)}


def _post(client, **extra):
    body = {
        "message_id": f"<{uuid.uuid4()}@x>", "subject": "Opportunity at Acme",
        "sender": "Jane Smith via LinkedIn <inmail-hit-reply@linkedin.com>",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "category": "recruiter_outreach", "confidence": 0.93, "postings": [], **extra,
    }
    r = client.post("/agent/inbox", json=body, headers=AGENT)
    assert r.status_code == 201, r.text


def _row(client):
    return client.get("/agent/inbox").json()["items"][0]


def test_zero_posting_recruiter_mail_carries_its_card(client):
    _post(client, recruiter={"name": "Jane Smith", "email": "Jane@Acme.example", "title": "Senior Recruiter",
                             "employer": "Acme", "linkedin_url": "https://www.linkedin.com/in/janesmith",
                             "is_agency": False, "recruiter_confidence": 0.9})
    row = _row(client)
    assert row["postings"] == []
    assert row["recruiter"] == {"name": "Jane Smith", "email": "jane@acme.example", "phone": None,
                                "title": "Senior Recruiter", "employer": "Acme",
                                "linkedin_url": "https://www.linkedin.com/in/janesmith", "type": "in_house"}


def test_card_is_sanitised_like_the_recruiters_page(client):
    """A javascript: link must never reach the page as a link (C2)."""
    _post(client, recruiter={"name": "X" * 500, "linkedin_url": "javascript:alert(document.cookie)"})
    rec = _row(client)["recruiter"]
    assert rec["linkedin_url"] is None
    assert len(rec["name"]) < 500


def test_no_card_no_field(client):
    _post(client)
    assert _row(client)["recruiter"] is None
