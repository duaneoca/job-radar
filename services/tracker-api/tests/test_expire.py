"""Tests for soft-expiry of unactioned jobs (_do_expire) and the
expire → cleanup lifecycle."""

import uuid
from datetime import datetime, timedelta, timezone

from app import models
from app.config import settings
from app.routers.admin import _do_cleanup, _do_expire

from .conftest import TEST_USER_ID


def _make_job(db, title="A job") -> models.Job:
    job = models.Job(
        id=uuid.uuid4(),
        title=title,
        company="Acme",
        url=f"https://jobs.example.com/{uuid.uuid4()}",
        source="manual",
    )
    db.add(job)
    db.flush()
    return job


def _make_review(db, status: models.JobStatus, age_days: int) -> models.UserJobReview:
    """Create a review for the test user with updated_at age_days in the past."""
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    job = _make_job(db)
    review = models.UserJobReview(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        job_id=job.id,
        status=status,
        created_at=ts,
        updated_at=ts,
    )
    db.add(review)
    db.commit()
    return review


def test_expire_flips_old_unactioned_reviews(db, test_user):
    """NEW/REVIEWED older than job_ttl_days → EXPIRED."""
    old_new = _make_review(db, models.JobStatus.NEW, settings.job_ttl_days + 5)
    old_reviewed = _make_review(db, models.JobStatus.REVIEWED, settings.job_ttl_days + 5)

    result = _do_expire(db)

    assert result["reviews_expired"] == 2
    db.refresh(old_new)
    db.refresh(old_reviewed)
    assert old_new.status == models.JobStatus.EXPIRED
    assert old_reviewed.status == models.JobStatus.EXPIRED


def test_expire_leaves_recent_reviews_untouched(db, test_user):
    """NEW/REVIEWED younger than job_ttl_days are not expired."""
    fresh = _make_review(db, models.JobStatus.NEW, settings.job_ttl_days - 5)

    result = _do_expire(db)

    assert result["reviews_expired"] == 0
    db.refresh(fresh)
    assert fresh.status == models.JobStatus.NEW


def test_expire_ignores_actioned_and_terminal_statuses(db, test_user):
    """Applied/interviewing/offer and already-terminal statuses are never expired,
    even when very old."""
    applied = _make_review(db, models.JobStatus.APPLIED, 365)
    interviewing = _make_review(db, models.JobStatus.INTERVIEWING, 365)
    offer = _make_review(db, models.JobStatus.OFFER, 365)
    dismissed = _make_review(db, models.JobStatus.DISMISSED, 365)

    result = _do_expire(db)

    assert result["reviews_expired"] == 0
    for r in (applied, interviewing, offer, dismissed):
        db.refresh(r)
    assert applied.status == models.JobStatus.APPLIED
    assert interviewing.status == models.JobStatus.INTERVIEWING
    assert offer.status == models.JobStatus.OFFER
    assert dismissed.status == models.JobStatus.DISMISSED


def test_expire_resets_updated_at_so_cleanup_grace_starts_now(db, test_user):
    """A freshly expired review is NOT immediately deleted by cleanup — its
    updated_at is reset, giving it the full terminal_ttl_days grace window."""
    _make_review(db, models.JobStatus.NEW, settings.job_ttl_days + 100)

    _do_expire(db)
    cleanup_result = _do_cleanup(db)

    # Expired just now → still within terminal grace → not deleted yet.
    assert cleanup_result["reviews_deleted"] == 0
    remaining = db.query(models.UserJobReview).count()
    assert remaining == 1


def test_cleanup_deletes_long_expired_reviews(db, test_user):
    """An EXPIRED review older than terminal_ttl_days is hard-deleted, and its
    now-orphaned job goes with it."""
    _make_review(db, models.JobStatus.EXPIRED, settings.terminal_ttl_days + 5)

    result = _do_cleanup(db)

    assert result["reviews_deleted"] == 1
    assert result["orphan_jobs_deleted"] == 1
    assert db.query(models.UserJobReview).count() == 0
    assert db.query(models.Job).count() == 0
