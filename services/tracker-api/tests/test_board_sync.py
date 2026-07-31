"""Company-board absence expiry.

A posting that stops appearing on the employer's own board has been taken down —
boards return every open role, so absence is real evidence. The tests that matter
here are the ones about NOT expiring: a failed fetch, a single bad scrape, and a
job the user has already acted on.
"""

import uuid
from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID

JOB = {"title": "Eng", "company": "Acme", "url": "https://x/1", "source": "greenhouse"}


def _job(db, ext_id, company="Acme", source="greenhouse", status=models.JobStatus.NEW):
    job = models.Job(
        id=uuid.uuid4(), external_id=ext_id, source=source,
        title=f"Job {ext_id}", company=company, url=f"https://x/{ext_id}",
    )
    db.add(job)
    db.flush()
    review = models.UserJobReview(
        id=uuid.uuid4(), user_id=TEST_USER_ID, job_id=job.id, status=status,
    )
    db.add(review)
    db.commit()
    return job, review


def _sync(client, companies, source="greenhouse"):
    return client.post("/jobs/board-sync", json={"source": source, "companies": companies})


# ── the signal works ──────────────────────────────────────────

def test_missing_posting_expires_after_two_strikes(client, db):
    job, review = _job(db, "gone-1")

    r1 = _sync(client, {"Acme": ["still-here"]})
    assert r1.status_code == 200
    assert r1.json() == {"missing": 1, "expired": 0, "reset": 0}
    db.refresh(review)
    assert review.status == models.JobStatus.NEW      # one strike is not enough

    r2 = _sync(client, {"Acme": ["still-here"]})
    assert r2.json()["expired"] == 1
    db.refresh(review)
    assert review.status == models.JobStatus.EXPIRED


def test_posting_still_on_the_board_is_untouched(client, db):
    job, review = _job(db, "present")
    for _ in range(3):
        assert _sync(client, {"Acme": ["present", "other"]}).json()["missing"] == 0
    db.refresh(review)
    assert review.status == models.JobStatus.NEW


def test_reappearing_resets_the_counter(client, db):
    """One flaky scrape must not accumulate toward expiry."""
    job, review = _job(db, "flaky")

    _sync(client, {"Acme": []})                        # strike 1
    db.refresh(job)
    assert job.board_missing_count == 1

    r = _sync(client, {"Acme": ["flaky"]})             # back on the board
    assert r.json()["reset"] == 1
    db.refresh(job)
    assert job.board_missing_count == 0

    _sync(client, {"Acme": []})                        # strike 1 again, not 2
    db.refresh(review)
    assert review.status == models.JobStatus.NEW


def test_empty_board_expires_its_jobs(client, db):
    """A board that genuinely returned zero roles is evidence, not an error —
    the scraper only sends companies whose board it actually read."""
    job, review = _job(db, "last-one")
    _sync(client, {"Acme": []})
    _sync(client, {"Acme": []})
    db.refresh(review)
    assert review.status == models.JobStatus.EXPIRED


# ── the ways this could go wrong ──────────────────────────────

def test_company_absent_from_the_payload_is_never_touched(client, db):
    """A board we failed to fetch is simply omitted by the scraper. Its jobs must
    not be expired — this is the mass-expiry failure mode."""
    job, review = _job(db, "unreachable", company="Unreachable Inc")

    for _ in range(5):
        _sync(client, {"Acme": ["something"]})         # a different company

    db.refresh(job)
    db.refresh(review)
    assert job.board_missing_count == 0
    assert review.status == models.JobStatus.NEW


def test_other_companies_are_not_collateral(client, db):
    job_a, rev_a = _job(db, "a1", company="Acme")
    job_b, rev_b = _job(db, "b1", company="Globex")

    _sync(client, {"Acme": []})
    _sync(client, {"Acme": []})

    db.refresh(rev_a); db.refresh(rev_b)
    assert rev_a.status == models.JobStatus.EXPIRED
    assert rev_b.status == models.JobStatus.NEW


def test_actioned_jobs_are_never_expired(client, db):
    """You applied to it. The posting coming down must not rewrite your record."""
    for st in (models.JobStatus.APPLIED, models.JobStatus.INTERVIEWING,
               models.JobStatus.OFFER, models.JobStatus.REFERRAL_REQUESTED):
        job, review = _job(db, f"acted-{st.value}", status=st)
        _sync(client, {"Acme": []})
        _sync(client, {"Acme": []})
        db.refresh(review)
        assert review.status == st


def test_other_sources_are_not_affected(client, db):
    """Only the source being synced is considered — an Ashby sync must not touch
    Greenhouse jobs at the same company."""
    gh, gh_rev = _job(db, "same-id", source="greenhouse")
    ash, ash_rev = _job(db, "same-id", source="ashby")

    _sync(client, {"Acme": []}, source="ashby")
    _sync(client, {"Acme": []}, source="ashby")

    db.refresh(gh_rev); db.refresh(ash_rev)
    assert ash_rev.status == models.JobStatus.EXPIRED
    assert gh_rev.status == models.JobStatus.NEW


def test_aggregator_sources_are_rejected(client, db):
    """Absence means nothing for a truncated search result — refuse outright."""
    for src in ("adzuna", "jsearch", "the_muse", "manual"):
        r = _sync(client, {"Acme": []}, source=src)
        assert r.status_code == 400, src


def test_expiry_is_global_across_users(client, db):
    """Board presence is a property of the posting, not of one user's view."""
    job, mine = _job(db, "shared")
    other_user = models.User(
        id=uuid.uuid4(), email="other@x.com", password_hash="x", is_approved=True,
    )
    db.add(other_user)
    db.flush()
    theirs = models.UserJobReview(
        id=uuid.uuid4(), user_id=other_user.id, job_id=job.id,
        status=models.JobStatus.REVIEWED,
    )
    db.add(theirs)
    db.commit()

    _sync(client, {"Acme": []})
    _sync(client, {"Acme": []})

    db.refresh(mine); db.refresh(theirs)
    assert mine.status == models.JobStatus.EXPIRED
    assert theirs.status == models.JobStatus.EXPIRED


def test_new_jobs_start_at_zero_misses(client, db):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    job = db.query(models.Job).filter(models.Job.source == "greenhouse").first()
    assert job.board_missing_count == 0
