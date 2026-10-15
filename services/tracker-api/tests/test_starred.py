"""Starring a job: the user's own "come back to this" marker.

It is filterable in the list, and because the user is telling us they mean to
return, the guess-based expiry rules skip it and cleanup never deletes it.
Board-sync, which is evidence the posting is gone, still marks it Expired.
"""

from unittest.mock import patch

from app import models
from app.config import settings
from app.merge_duplicates import Row
from app.routers.admin import _do_cleanup, _do_expire

from .conftest import TEST_USER_ID
from .test_board_sync import _job, _sync
from .test_expire import _make_review


def _post(client, n):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json={
            "title": f"Engineer {n}", "company": f"Co {n}", "url": f"https://x/{n}", "source": "manual"})


def test_star_round_trips_and_filters(client):
    _post(client, 1)
    _post(client, 2)
    items = client.get("/jobs").json()["items"]
    assert all(i["starred"] is False for i in items)

    target = items[0]["id"]
    r = client.patch(f"/jobs/{target}", json={"starred": True})
    assert r.status_code == 200 and r.json()["starred"] is True
    assert client.get(f"/jobs/{target}").json()["starred"] is True

    starred = client.get("/jobs?starred=true").json()
    assert starred["total"] == 1 and starred["items"][0]["id"] == target
    unstarred = client.get("/jobs?starred=false").json()
    assert unstarred["total"] == 1 and unstarred["items"][0]["id"] != target
    assert client.get("/jobs").json()["total"] == 2

    client.patch(f"/jobs/{target}", json={"starred": False})
    assert client.get("/jobs?starred=true").json()["total"] == 0


def test_starring_does_not_touch_status(client):
    _post(client, 1)
    rid = client.get("/jobs").json()["items"][0]["id"]
    before = client.get(f"/jobs/{rid}").json()["timeline"]
    client.patch(f"/jobs/{rid}", json={"starred": True})
    body = client.get(f"/jobs/{rid}").json()
    assert body["status"] == "new"
    assert body["timeline"] == before


def test_guess_based_expiry_skips_starred(db, test_user):
    stale_row = _make_review(db, models.JobStatus.NEW, settings.job_ttl_days + 5)
    old_posting = _make_review(db, models.JobStatus.NEW, age_days=0, source="adzuna",
                               posted_days_ago=settings.posting_max_age_days + 5)
    for r in (stale_row, old_posting):
        r.starred = True
    db.commit()

    assert _do_expire(db)["reviews_expired"] == 0
    for r in (stale_row, old_posting):
        db.refresh(r)
        assert r.status == models.JobStatus.NEW


def test_unstarred_neighbours_still_expire(db, test_user):
    """The exemption is per review, not a switch that turns expiry off."""
    starred = _make_review(db, models.JobStatus.NEW, settings.job_ttl_days + 5)
    starred.starred = True
    plain = _make_review(db, models.JobStatus.NEW, settings.job_ttl_days + 5)
    db.commit()

    assert _do_expire(db)["reviews_expired"] == 1
    db.refresh(plain)
    assert plain.status == models.JobStatus.EXPIRED


def test_board_sync_still_expires_starred(client, db):
    """The employer took it down: that is news the user wants, star or not."""
    _, review = _job(db, "gone-1")
    review.starred = True
    db.commit()
    _sync(client, {"Acme": ["still-here"]})
    _sync(client, {"Acme": ["still-here"]})
    db.refresh(review)
    assert review.status == models.JobStatus.EXPIRED and review.starred is True


def test_cleanup_never_deletes_starred(db, test_user):
    for status in (models.JobStatus.EXPIRED, models.JobStatus.DISMISSED, models.JobStatus.REJECTED):
        r = _make_review(db, status, settings.terminal_ttl_days + 30)
        r.starred = True
    _make_review(db, models.JobStatus.EXPIRED, settings.terminal_ttl_days + 30)  # unstarred
    db.commit()

    result = _do_cleanup(db)

    assert result["reviews_deleted"] == 1
    assert db.query(models.UserJobReview).filter_by(starred=True).count() == 3


def test_duplicate_merge_treats_a_star_as_work():
    row = Row(review_id=1, job_id=1, status="new", source="adzuna", url="u",
              match_key="k", scraped=None, work={"starred": True})
    assert row.touched and not row.deletable
    assert not Row(review_id=2, job_id=2, status="new", source="adzuna", url="u",
                   match_key="k", scraped=None, work={"starred": False}).touched
