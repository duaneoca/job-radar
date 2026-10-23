"""Add a job from a recruiter email in the inbox.

The agent sends the pitched role with its details (location, remote, annual pay,
description); the inbox list stays slim, the Add Job page reads one posting in
full, and saving marks the posting imported with the job it became. Recruiter
postings rarely have a link, so a manual job may have none."""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID

AGENT = {"X-User-Id": str(TEST_USER_ID)}    # the cloud agent's per-user header
PITCH = ("Contract (C2C) · On-site · $70–80/hr (≈ $145,600–166,400/yr at 40 h/week)\n\n"
         "Hi Duane,\nRole: Sr. Agentic AI Engineer\nResponsibilities:\n- Build agents")


def _post_recruiter_email(client, **posting):
    body = {
        "message_id": f"<{uuid.uuid4()}@x>", "subject": "Urgent Requirement : Sr. Agentic AI Engineer",
        "sender": "Amrita <amrita@examplestaffing.com>", "received_at": datetime.now(timezone.utc).isoformat(),
        "category": "recruiter_outreach", "confidence": 0.97,
        "postings": [{"company": "Example Staffing Inc (agency)", "role": "Sr. Agentic AI Engineer",
                      "location": "Santa Clara, CA", "remote": False,
                      "salary_min": 145600, "salary_max": 166400, "description": PITCH, **posting}],
    }
    r = client.post("/agent/inbox", json=body, headers=AGENT)
    assert r.status_code == 201, r.text
    return r.json()["posting_ids"][0]


def _list_posting(client, pid):
    for e in client.get("/agent/inbox").json()["items"]:
        for p in e["postings"]:
            if p["id"] == pid:
                return p


def test_details_are_stored_and_the_list_stays_slim(client):
    pid = _post_recruiter_email(client)
    p = _list_posting(client, pid)
    assert p["location"] == "Santa Clara, CA" and p["remote"] is False
    assert (p["salary_min"], p["salary_max"]) == (145600, 166400)
    assert "description" not in p                      # never in lists

    d = client.get(f"/agent/inbox/postings/{pid}").json()
    assert d["description"] == PITCH
    assert d["email_category"] == "recruiter_outreach"
    assert d["email_subject"].startswith("Urgent Requirement")
    assert d["link"] is None and d["import_status"] == "pending"


def test_untrusted_details_are_capped_and_insane_salaries_dropped(client):
    pid = _post_recruiter_email(client, description="x" * 50_000, location="L" * 500,
                                salary_min=5, salary_max=99_000_000)
    d = client.get(f"/agent/inbox/postings/{pid}").json()
    assert len(d["description"]) == 20_000 and len(d["location"]) == 200
    assert d["salary_min"] is None and d["salary_max"] is None


def test_an_older_agent_without_details_still_works(client):
    r = client.post("/agent/inbox", json={
        "message_id": "<old@x>", "subject": "s", "sender": "a@b.c",
        "received_at": datetime.now(timezone.utc).isoformat(), "category": "job_alert", "confidence": 1.0,
        "postings": [{"company": "Acme", "role": "Eng", "link": "https://x/1"}]}, headers=AGENT)
    pid = r.json()["posting_ids"][0]
    d = client.get(f"/agent/inbox/postings/{pid}").json()
    assert d["description"] is None and d["location"] is None


def test_a_job_can_be_added_without_a_link(client):
    with patch("app.routers.jobs._celery"):
        r1 = client.post("/jobs/manual", json={"title": "Sr. Agentic AI Engineer", "company": "Example Staffing Inc (agency)",
                                               "location": "Santa Clara, CA", "description": PITCH,
                                               "salary_min": 145600, "salary_max": 166400})
        r2 = client.post("/jobs/manual", json={"title": "Data Architect", "company": "Other Staffing (agency)"})
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    assert r1.json()["url"] == "" and r1.json()["salary_min"] == 145600
    # Two link-less jobs must not be mistaken for the same job.
    assert r1.json()["id"] != r2.json()["id"]


def test_an_empty_link_never_replaces_an_aggregator_copys_real_one(client, db):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json={"title": "Eng", "company": "Acme", "location": "Seattle",
                                                           "url": "https://adzuna/1", "source": "adzuna"})
        r = client.post("/jobs/manual", json={"title": "Eng", "company": "Acme", "location": "Seattle"})
    assert r.json()["already_had"] == "same_role" and r.json()["url"] == "https://adzuna/1"


def test_saving_marks_the_posting_imported(client):
    pid = _post_recruiter_email(client)
    with patch("app.routers.jobs._celery"):
        review = client.post("/jobs/manual", json={"title": "Sr. Agentic AI Engineer",
                                                   "company": "Example Staffing Inc (agency)"}).json()
    r = client.post(f"/agent/inbox/postings/{pid}/imported", json={"review_id": review["id"]})
    assert r.status_code == 200 and r.json()["import_status"] == "imported"
    assert _list_posting(client, pid)["imported_review_id"] == review["id"]


def test_imported_must_point_at_the_users_own_job(client, db):
    pid = _post_recruiter_email(client)
    other = models.User(id=uuid.uuid4(), email="o@example.com", password_hash="x", is_approved=True)
    job = models.Job(id=uuid.uuid4(), title="T", company="C", url="", source="manual")
    db.add_all([other, job])
    db.flush()
    theirs = models.UserJobReview(id=uuid.uuid4(), user_id=other.id, job_id=job.id)
    db.add(theirs)
    db.commit()
    assert client.post(f"/agent/inbox/postings/{pid}/imported", json={"review_id": str(theirs.id)}).status_code == 404
    assert _list_posting(client, pid)["import_status"] == "pending"


def test_another_users_posting_is_not_found(client, db):
    pid = _post_recruiter_email(client)
    other = models.User(id=uuid.uuid4(), email="o2@example.com", password_hash="x", is_approved=True)
    db.add(other)
    db.commit()
    db.query(models.InboxPosting).filter_by(id=uuid.UUID(pid)).update({"user_id": other.id})
    db.commit()
    assert client.get(f"/agent/inbox/postings/{pid}").status_code == 404
