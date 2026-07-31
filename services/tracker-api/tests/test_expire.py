"""Tests for soft-expiry of unactioned jobs (_do_expire) and the
expire → cleanup lifecycle."""

import uuid
from datetime import datetime, timedelta, timezone

from app import models
from app.config import settings
from app.routers.admin import _do_cleanup, _do_expire

from .conftest import TEST_USER_ID


def _make_job(db, title="A job", source="manual", posted=None) -> models.Job:
    job = models.Job(
        id=uuid.uuid4(),
        title=title,
        company="Acme",
        url=f"https://jobs.example.com/{uuid.uuid4()}",
        source=source,
        date_posted=posted,
    )
    db.add(job)
    db.flush()
    return job


def _make_review(db, status: models.JobStatus, age_days: int,
                 source="manual", posted_days_ago=None) -> models.UserJobReview:
    """Create a review for the test user with updated_at age_days in the past."""
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    posted = (datetime.now(timezone.utc) - timedelta(days=posted_days_ago)
              if posted_days_ago is not None else None)
    job = _make_job(db, source=source, posted=posted)
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


def test_referral_requested_is_never_expired_or_cleaned(db, test_user):
    """A pending referral is active work: it must survive both the soft-expiry
    sweep and the terminal cleanup no matter how old it is."""
    referral = _make_review(db, models.JobStatus.REFERRAL_REQUESTED, 365)

    expire_result = _do_expire(db)
    cleanup_result = _do_cleanup(db)

    assert expire_result["reviews_expired"] == 0
    assert cleanup_result["reviews_deleted"] == 0
    db.refresh(referral)
    assert referral.status == models.JobStatus.REFERRAL_REQUESTED


def test_lifecycle_status_sets_are_disjoint():
    """No status may be both expirable and terminal — otherwise a job could be
    expired and immediately swept. Guards future status additions."""
    from app.routers.admin import _EXPIRABLE_STATUSES, _TERMINAL_STATUSES

    assert not set(_EXPIRABLE_STATUSES) & set(_TERMINAL_STATUSES)


# ── rule 2: the posting itself is stale at the source ─────────

def test_old_posting_expires_even_when_freshly_scraped(db, test_user):
    """The listing is what goes dead, not our row. A job scraped today but posted
    a month ago is already gone from the employer's site."""
    r = _make_review(db, models.JobStatus.NEW, age_days=0,
                     source="adzuna", posted_days_ago=settings.posting_max_age_days + 5)

    result = _do_expire(db)

    assert result["stale_postings"] == 1
    assert result["unactioned"] == 0            # our row is brand new
    db.refresh(r)
    assert r.status == models.JobStatus.EXPIRED


def test_recent_posting_survives(db, test_user):
    r = _make_review(db, models.JobStatus.NEW, age_days=0,
                     source="adzuna", posted_days_ago=settings.posting_max_age_days - 5)

    assert _do_expire(db)["stale_postings"] == 0
    db.refresh(r)
    assert r.status == models.JobStatus.NEW


def test_bookmarklet_imports_are_exempt(db, test_user):
    """A job the user captured deliberately is not expired for being old."""
    r = _make_review(db, models.JobStatus.NEW, age_days=0,
                     source="manual", posted_days_ago=settings.posting_max_age_days + 60)

    assert _do_expire(db)["stale_postings"] == 0
    db.refresh(r)
    assert r.status == models.JobStatus.NEW


def test_missing_date_posted_is_not_expired(db, test_user):
    """No date_posted means no evidence of staleness — rule 1 still governs."""
    r = _make_review(db, models.JobStatus.NEW, age_days=0,
                     source="adzuna", posted_days_ago=None)

    assert _do_expire(db)["stale_postings"] == 0
    db.refresh(r)
    assert r.status == models.JobStatus.NEW


def test_applied_jobs_are_never_expired_by_posting_age(db, test_user):
    """You applied to it — the posting going down must not touch your record."""
    for st in (models.JobStatus.APPLIED, models.JobStatus.INTERVIEWING,
               models.JobStatus.REFERRAL_REQUESTED):
        r = _make_review(db, st, age_days=0, source="adzuna",
                         posted_days_ago=settings.posting_max_age_days + 90)
        _do_expire(db)
        db.refresh(r)
        assert r.status == st


def test_both_rules_counted_separately_without_double_counting(db, test_user):
    old_row = _make_review(db, models.JobStatus.NEW, age_days=settings.job_ttl_days + 5)
    old_posting = _make_review(db, models.JobStatus.NEW, age_days=0, source="adzuna",
                               posted_days_ago=settings.posting_max_age_days + 5)
    # qualifies for BOTH rules — must only be counted once
    both = _make_review(db, models.JobStatus.NEW, age_days=settings.job_ttl_days + 5,
                        source="adzuna", posted_days_ago=settings.posting_max_age_days + 5)

    result = _do_expire(db)

    assert result["reviews_expired"] == 3
    assert result["unactioned"] == 2            # old_row + both
    assert result["stale_postings"] == 1        # old_posting only; `both` already flipped
    for r in (old_row, old_posting, both):
        db.refresh(r)
        assert r.status == models.JobStatus.EXPIRED


def test_company_board_sources_are_exempt_from_posting_age(db, test_user):
    """We re-read these boards every scrape, so a posting still being returned is
    still open — some evergreen reqs sit on a board for a year or more."""
    for src in ("greenhouse", "ashby", "lever"):
        r = _make_review(db, models.JobStatus.NEW, age_days=0, source=src,
                         posted_days_ago=settings.posting_max_age_days + 300)
        assert _do_expire(db)["stale_postings"] == 0, src
        db.refresh(r)
        assert r.status == models.JobStatus.NEW, src


def test_aggregator_sources_are_not_exempt(db, test_user):
    """The aggregators give us no evidence of liveness, so age governs."""
    for src in ("adzuna", "jsearch", "the_muse", "remotive"):
        r = _make_review(db, models.JobStatus.NEW, age_days=0, source=src,
                         posted_days_ago=settings.posting_max_age_days + 5)
        assert _do_expire(db)["stale_postings"] == 1, src
        db.refresh(r)
        assert r.status == models.JobStatus.EXPIRED, src
