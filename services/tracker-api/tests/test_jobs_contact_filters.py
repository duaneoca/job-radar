"""Job list: LinkedIn-contact match (computed has_contact) + multi-value filters."""

from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID

JOB = {"title": "Eng", "company": "Acme Corp", "url": "https://x/1", "source": "manual"}


def _scrape(client, **over):
    with patch("app.routers.jobs._celery"):
        return client.post(f"/jobs?user_id={TEST_USER_ID}", json={**JOB, **over})


def _add_connection(db, company):
    db.add(models.LinkedInConnection(user_id=TEST_USER_ID, first_name="A", last_name="B", company=company))
    db.commit()


def test_has_contact_true_on_company_match(client, db):
    _scrape(client, company="Acme Corp")
    _add_connection(db, "  acme corp ")  # different case + whitespace still matches
    body = client.get("/jobs").json()
    assert body["items"][0]["has_contact"] is True


def test_has_contact_false_without_match(client, db):
    _scrape(client, company="Acme Corp")
    _add_connection(db, "Other Inc")
    body = client.get("/jobs").json()
    assert body["items"][0]["has_contact"] is False


def test_has_contact_filter_true(client, db):
    _scrape(client, company="Acme Corp", url="https://x/1", external_id="1")
    _scrape(client, company="Nobody LLC", url="https://x/2", external_id="2")
    _add_connection(db, "Acme Corp")
    only = client.get("/jobs", params={"has_contact": True}).json()
    assert only["total"] == 1
    assert only["items"][0]["company"] == "Acme Corp"


def test_has_contact_filter_false(client, db):
    _scrape(client, company="Acme Corp", url="https://x/1", external_id="1")
    _scrape(client, company="Nobody LLC", url="https://x/2", external_id="2")
    _add_connection(db, "Acme Corp")
    none = client.get("/jobs", params={"has_contact": False}).json()
    assert none["total"] == 1
    assert none["items"][0]["company"] == "Nobody LLC"


def test_multi_value_status_filter(client, db):
    _scrape(client, url="https://x/1", external_id="1")
    _scrape(client, url="https://x/2", external_id="2")
    _scrape(client, url="https://x/3", external_id="3")
    reviews = db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).all()
    reviews[0].status = models.JobStatus.APPLIED
    reviews[1].status = models.JobStatus.REJECTED
    # reviews[2] stays NEW
    db.commit()

    both = client.get("/jobs", params={"status": ["applied", "rejected"]}).json()
    assert both["total"] == 2
    one = client.get("/jobs", params={"status": ["applied"]}).json()
    assert one["total"] == 1


def test_multi_value_source_filter(client, db):
    _scrape(client, url="https://x/1", external_id="1", source="manual")
    _scrape(client, url="https://x/2", external_id="2", source="adzuna")
    _scrape(client, url="https://x/3", external_id="3", source="remotive")
    body = client.get("/jobs", params={"source": ["manual", "adzuna"]}).json()
    assert body["total"] == 2
