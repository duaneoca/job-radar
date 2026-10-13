"""Making "possible duplicate" in the Inbox something you can act on.

Before: a bare badge — it didn't say which job it matched, there was nothing to
do about it, and importing it via the bookmarklet added a second copy (and
scored it again) whenever the link differed from the copy already held.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID

ROLE = {"title": "Principal AI Forward Deployed Engineer", "company": "ServiceNow",
        "location": "Santa Clara, CA", "description": "d"}


def _scrape(client, **over):
    body = {**ROLE, "url": "https://adzuna/redirect/1", "source": "adzuna", "external_id": "a1", **over}
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json=body)
    return client.get("/jobs").json()["items"][0]


def _import(client, separate=False, **over):
    body = {**ROLE, "url": "https://careers.servicenow.com/jobs/744000154008689", "source": "manual", **over}
    with patch("app.routers.jobs._celery") as celery:
        r = client.post("/jobs/manual" + ("?separate=true" if separate else ""), json=body)
    return r, celery


def _inbox(db, matched_review_id):
    email = models.InboxEmail(user_id=TEST_USER_ID, message_id=f"<{uuid.uuid4()}>", subject="alert",
                              sender="jobs@linkedin.com", received_at=datetime.now(timezone.utc),
                              category=models.EmailCategory.JOB_ALERT, confidence=1.0,
                              status=models.EmailStatus.PROCESSED)
    db.add(email)
    db.flush()
    posting = models.InboxPosting(inbox_email_id=email.id, user_id=TEST_USER_ID, company="ServiceNow",
                                  role=ROLE["title"], link="https://linkedin/job/1",
                                  possible_duplicate=True, matched_review_id=matched_review_id)
    db.add(posting)
    db.commit()
    return posting


# ── 1. the badge names what it matched ────────────────────────

def test_inbox_names_the_matched_job(client, db):
    job = _scrape(client)
    _inbox(db, uuid.UUID(job["id"]))
    p = client.get("/agent/inbox").json()["items"][0]["postings"][0]
    assert p["matched_job"]["title"] == ROLE["title"]
    assert p["matched_job"]["status"] == "new"


def test_another_users_review_is_never_named(client, db):
    """matched_review_id comes from the agent — untrusted (H1)."""
    other = models.User(email="other@x", password_hash="h", is_approved=True)
    db.add(other)
    db.flush()
    j = models.Job(title="Secret", company="Other Co", url="https://o/1", source="manual")
    db.add(j)
    db.flush()
    rev = models.UserJobReview(user_id=other.id, job_id=j.id)
    db.add(rev)
    db.commit()
    _inbox(db, rev.id)
    p = client.get("/agent/inbox").json()["items"][0]["postings"][0]
    assert p["matched_job"] is None


# ── 2. a posting can be skipped, and un-skipped ───────────────

def test_skip_and_undo(client, db):
    posting = _inbox(db, None)
    r = client.patch(f"/agent/inbox/postings/{posting.id}", json={"import_status": "dismissed"})
    assert r.status_code == 200 and r.json()["import_status"] == "dismissed"
    r = client.patch(f"/agent/inbox/postings/{posting.id}", json={"import_status": "pending"})
    assert r.json()["import_status"] == "pending"


def test_imported_cannot_be_set_or_overwritten(client, db):
    posting = _inbox(db, None)
    assert client.patch(f"/agent/inbox/postings/{posting.id}",
                        json={"import_status": "imported"}).status_code == 422
    posting.import_status = models.ImportStatus.IMPORTED
    db.commit()
    assert client.patch(f"/agent/inbox/postings/{posting.id}",
                        json={"import_status": "dismissed"}).status_code == 409


def test_someone_elses_posting_is_not_found(client, db):
    assert client.patch(f"/agent/inbox/postings/{uuid.uuid4()}",
                        json={"import_status": "dismissed"}).status_code == 404


# ── 3. importing a role you already have ──────────────────────

def test_import_of_a_role_you_have_returns_yours(client, db):
    existing = _scrape(client)
    r, celery = _import(client)
    assert r.status_code == 200
    body = r.json()
    assert body["already_had"] == "same_role" and body["id"] == existing["id"]
    celery.send_task.assert_not_called()                          # not scored again
    assert db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).count() == 1


def test_your_imported_link_replaces_an_aggregator_link(client, db):
    _scrape(client)
    body = _import(client)[0].json()
    assert body["url"] == "https://careers.servicenow.com/jobs/744000154008689"
    assert any("Link updated" in e["description"] for e in body["timeline"])


def test_the_shared_job_row_is_not_edited(client, db):
    """Job rows are shared between users; the imported link goes on a new row."""
    _scrape(client)
    old = db.query(models.Job).filter_by(external_id="a1").one()
    _import(client)
    db.refresh(old)
    assert old.url == "https://adzuna/redirect/1"


def test_a_board_link_is_kept(client, db):
    _scrape(client, source="greenhouse", external_id="g1", url="https://boards.greenhouse.io/sn/1")
    body = _import(client)[0].json()
    assert body["url"] == "https://boards.greenhouse.io/sn/1" and body["already_had"] == "same_role"


def test_add_as_separate_job_anyway(client, db):
    _scrape(client)
    r, celery = _import(client, separate=True)
    assert r.status_code == 201 and r.json()["already_had"] is None
    celery.send_task.assert_called_once()
    assert db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).count() == 2


def test_same_link_is_still_reported_as_such(client, db):
    _import(client)
    r, _ = _import(client)
    assert r.status_code == 200 and r.json()["already_had"] == "same_link"
