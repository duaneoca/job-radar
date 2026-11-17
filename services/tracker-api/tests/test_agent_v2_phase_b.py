"""Agent V2 Phase B (INTEGRATION_SPEC §3.6): one inbox posting per
(user, dedup_key), and a repeat is SKIPPED — never a 4xx for the email.

The cloud agent keeps no state between runs, so this index is its only
cross-email duplicate memory."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app import models
from app.security import hash_password

from .conftest import TEST_USER_ID

AGENT = {"X-User-Id": str(TEST_USER_ID)}
KEY = "linkedin:4012345678"


def _post(client, postings, headers=AGENT, raw=None):
    r = client.post("/agent/inbox", headers=headers, json={
        "message_id": f"<{uuid.uuid4()}@x>", "subject": "Jobs for you",
        "sender": "LinkedIn <jobs@linkedin.com>", "received_at": datetime.now(timezone.utc).isoformat(),
        "category": "job_alert", "confidence": 0.99, "postings": postings,
        **({"raw_extracted_json": raw} if raw else {}),
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_a_repeat_in_a_later_email_is_skipped_and_the_email_still_lands(client, db):
    _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY}])
    second = _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY},
                            {"company": "Beta", "role": "PM", "dedup_key": "linkedin:999"}])
    assert len(second["posting_ids"]) == 1                       # only Beta stored
    assert db.query(models.InboxPosting).filter_by(dedup_key=KEY).count() == 1
    assert db.get(models.InboxEmail, uuid.UUID(second["inbox_email_id"])) is not None


def test_a_repeat_within_one_email_is_stored_once(client, db):
    out = _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY},
                         {"company": "Acme", "role": "Eng", "dedup_key": KEY}])
    assert len(out["posting_ids"]) == 1


def test_postings_without_a_key_are_never_constrained(client, db):
    _post(client, [{"company": "Acme", "role": "Eng"}])
    out = _post(client, [{"company": "Acme", "role": "Eng"}])
    assert len(out["posting_ids"]) == 1
    assert db.query(models.InboxPosting).filter(models.InboxPosting.dedup_key.is_(None)).count() == 2


def test_the_same_key_for_another_user_is_not_a_duplicate(client, db):
    """Multi-user: the index is per user, so one user's inbox never hides another's."""
    other = uuid.uuid4()
    db.add(models.User(id=other, email=f"{other}@example.com", password_hash=hash_password("x"),
                       full_name="O", is_approved=True, is_admin=False))
    db.commit()
    _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY}])
    out = _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY}],
                headers={"X-User-Id": str(other)})
    assert len(out["posting_ids"]) == 1


def test_a_skipped_posting_is_still_named_on_the_row(client, db):
    """The agent records every verified pick; the skipped one shows as
    "already listed from an earlier email" rather than vanishing."""
    picks = {"postings": [{"link_id": 1, "title": "Eng", "company": "Acme"}]}
    _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY}], raw=picks)
    _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY}], raw=picks)
    newest = client.get("/agent/inbox").json()["items"][0]
    assert newest["postings"] == []
    assert [(s["company"], s["role"]) for s in newest["seen_elsewhere"]] == [("Acme", "Eng")]


def test_a_race_past_the_lookup_is_still_a_skip(client, db, monkeypatch):
    """Two runs can both pass the lookup; the index decides, and the loser
    loses one posting, not the email."""
    _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY}])
    real_query = db.query

    def blind_query(*args, **kw):            # make the pre-check see nothing
        q = real_query(*args, **kw)
        if args and args[0] is models.InboxPosting.dedup_key:
            return q.filter(False)
        return q
    monkeypatch.setattr(db, "query", blind_query)
    out = _post(client, [{"company": "Acme", "role": "Eng", "dedup_key": KEY},
                         {"company": "Beta", "role": "PM"}])
    assert len(out["posting_ids"]) == 1


def test_the_database_itself_refuses_a_duplicate_key(db):
    """The guarantee is the index, not the pre-check."""
    email = models.InboxEmail(user_id=TEST_USER_ID, message_id="<m@x>", subject="s", sender="a@b.c",
                              received_at=datetime.now(timezone.utc), confidence=0.9,
                              category=models.EmailCategory.JOB_ALERT, status=models.EmailStatus.PROCESSED)
    db.add(email)
    db.flush()
    for _ in range(2):
        db.add(models.InboxPosting(inbox_email_id=email.id, user_id=TEST_USER_ID,
                                   company="A", role="R", dedup_key=KEY))
    with pytest.raises(IntegrityError):
        db.flush()
