"""An alert that repeats jobs an earlier alert already brought in: the agent
stores the repeats under the first email and records them in the later one's
raw_extracted_json.postings. The inbox names them, so the later email doesn't
look as if it lost half its jobs."""

import uuid
from datetime import datetime, timedelta, timezone

from app import models

from .conftest import TEST_USER_ID


def _email(db, subject, received, verified, stored, user_id=TEST_USER_ID):
    e = models.InboxEmail(
        id=uuid.uuid4(), user_id=user_id, message_id=f"<{uuid.uuid4()}@x>", subject=subject,
        sender="LinkedIn Job Alerts <a@linkedin.com>", received_at=received,
        category=models.EmailCategory.JOB_ALERT, confidence=1.0,
        raw_extracted_json={"pipeline": "v2-link-picker",
                            "postings": [{"link_id": i, "title": r, "company": c} for i, (c, r) in enumerate(verified)],
                            "duplicates_skipped": len(verified) - len(stored)},
        status=models.EmailStatus.PROCESSED,
    )
    db.add(e)
    db.flush()
    for c, r in stored:
        db.add(models.InboxPosting(inbox_email_id=e.id, user_id=user_id, company=c, role=r,
                                   link="https://www.linkedin.com/jobs/view/1"))
    db.commit()
    return e


def _by_id(client, eid):
    return next(i for i in client.get("/agent/inbox").json()["items"] if i["id"] == str(eid))


def test_repeats_are_named_with_the_email_that_has_them(client, db):
    t0 = datetime.now(timezone.utc) - timedelta(days=1)
    first = _email(db, "forward deployed engineer alert", t0,
                   verified=[("OpenAI", "Deployment Lead"), ("Google", "Customer Engineer")],
                   stored=[("OpenAI", "Deployment Lead"), ("Google", "Customer Engineer")])
    later = _email(db, "AI Solutions Architect alert", t0 + timedelta(hours=3),
                   verified=[("OpenAI", "Deployment Lead"), ("Ladders", "FDE - AI Security"),
                             ("Google", "Customer Engineer")],
                   stored=[("Ladders", "FDE - AI Security")])

    got = _by_id(client, later.id)
    assert [p["role"] for p in got["postings"]] == ["FDE - AI Security"]
    seen = {(s["company"], s["role"]): s for s in got["seen_elsewhere"]}
    assert set(seen) == {("OpenAI", "Deployment Lead"), ("Google", "Customer Engineer")}
    assert all(s["inbox_email_id"] == str(first.id) for s in seen.values())
    assert all(s["email_subject"] == "forward deployed engineer alert" for s in seen.values())
    assert seen[("OpenAI", "Deployment Lead")]["import_status"] == "pending"
    # The earlier copy's link, so the job opens without finding that email.
    assert all(s["link"] == "https://www.linkedin.com/jobs/view/1" for s in seen.values())
    assert _by_id(client, first.id)["seen_elsewhere"] == []


def test_imported_copy_links_to_the_job(client, db):
    t0 = datetime.now(timezone.utc) - timedelta(days=2)
    job = models.Job(id=uuid.uuid4(), title="Deployment Lead", company="OpenAI", url="https://x/1", source="manual")
    db.add(job)
    db.flush()
    review = models.UserJobReview(id=uuid.uuid4(), user_id=TEST_USER_ID, job_id=job.id)
    db.add(review)
    first = _email(db, "first", t0, verified=[("OpenAI", "Deployment Lead")], stored=[("OpenAI", "Deployment Lead")])
    p = db.query(models.InboxPosting).filter_by(inbox_email_id=first.id).one()
    p.import_status = models.ImportStatus.IMPORTED
    p.imported_review_id = review.id
    db.commit()
    later = _email(db, "later", t0 + timedelta(days=1), verified=[("OpenAI", "Deployment Lead")], stored=[])

    (s,) = _by_id(client, later.id)["seen_elsewhere"]
    assert s["import_status"] == "imported" and s["imported_review_id"] == str(review.id)


def test_matching_is_case_and_space_insensitive(client, db):
    t0 = datetime.now(timezone.utc) - timedelta(days=1)
    first = _email(db, "first", t0, verified=[("Reddit, Inc.", "Lead FDE")], stored=[("Reddit, Inc.", "Lead FDE")])
    later = _email(db, "later", t0 + timedelta(hours=1), verified=[("reddit, inc. ", " lead fde")], stored=[])
    (s,) = _by_id(client, later.id)["seen_elsewhere"]
    assert s["inbox_email_id"] == str(first.id)


def test_a_copy_that_is_gone_is_still_named(client, db):
    later = _email(db, "later", datetime.now(timezone.utc), verified=[("Acme", "Eng")], stored=[])
    (s,) = _by_id(client, later.id)["seen_elsewhere"]
    assert s["company"] == "Acme" and s["inbox_email_id"] is None and s["link"] is None


def test_other_users_copies_are_never_shown(client, db):
    other = models.User(id=uuid.uuid4(), email="o@example.com", password_hash="x", is_approved=True)
    db.add(other)
    db.commit()
    t0 = datetime.now(timezone.utc) - timedelta(days=1)
    _email(db, "theirs", t0, verified=[("Acme", "Eng")], stored=[("Acme", "Eng")], user_id=other.id)
    later = _email(db, "mine", t0 + timedelta(hours=1), verified=[("Acme", "Eng")], stored=[])
    (s,) = _by_id(client, later.id)["seen_elsewhere"]
    assert s["inbox_email_id"] is None


def test_emails_without_the_agents_record_are_unaffected(client, db):
    e = models.InboxEmail(id=uuid.uuid4(), user_id=TEST_USER_ID, message_id="<m@x>", subject="s",
                          sender="r@x", received_at=datetime.now(timezone.utc),
                          category=models.EmailCategory.RECRUITER_OUTREACH, confidence=0.9,
                          raw_extracted_json={"sorter": {}}, status=models.EmailStatus.PROCESSED)
    db.add(e)
    db.commit()
    assert _by_id(client, e.id)["seen_elsewhere"] == []
