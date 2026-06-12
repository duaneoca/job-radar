"""Tests for per-user job attribution on POST /jobs (Phase 4)."""

import uuid
from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID

JOB = {
    "title": "Forward Deployed Engineer",
    "company": "Acme",
    "url": "https://jobs.acme.com/1",
    "source": "adzuna",
    "external_id": "adz-1",
}


def _make_user(db, email):
    u = models.User(id=uuid.uuid4(), email=email, password_hash="x", is_approved=True)
    db.add(u)
    db.commit()
    return u


def test_user_id_scopes_review_no_fanout(client, db, test_user):
    """POST /jobs?user_id=X creates a review for X only — not the other user."""
    other = _make_user(db, "other@example.com")
    with patch("app.routers.jobs._celery"):
        resp = client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    assert resp.status_code == 201
    assert db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).count() == 1
    assert db.query(models.UserJobReview).filter_by(user_id=other.id).count() == 0


def test_user_id_attribution_is_idempotent(client, db, test_user):
    """Posting the same job twice for the same user → one Job, one review."""
    with patch("app.routers.jobs._celery"):
        r1 = client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
        r2 = client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    assert r1.status_code == 201
    assert r2.status_code == 200  # job already existed
    assert db.query(models.Job).count() == 1
    assert db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).count() == 1


def test_same_job_two_users_one_job_two_reviews(client, db, test_user):
    """The shared Job is deduped; each user gets their own review."""
    other = _make_user(db, "other2@example.com")
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
        client.post(f"/jobs?user_id={other.id}", json=JOB)
    assert db.query(models.Job).count() == 1
    assert db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).count() == 1
    assert db.query(models.UserJobReview).filter_by(user_id=other.id).count() == 1


def test_user_id_now_required(client, db, test_user):
    """Union mode is retired — POST /jobs without user_id is rejected (422)."""
    with patch("app.routers.jobs._celery"):
        resp = client.post("/jobs", json=JOB)
    assert resp.status_code == 422
