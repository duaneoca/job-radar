"""Tests for the /jobs endpoints — multi-user edition."""

from unittest.mock import patch

from .conftest import TEST_USER_ID

JOB_PAYLOAD = {
    "title": "Senior Python Engineer",
    "company": "Acme Corp",
    "url": "https://jobs.acme.com/123",
    "source": "manual",
    "location": "Austin, TX",
    "remote": True,
    "salary_min": 130000,
    "salary_max": 160000,
}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Scraper endpoint (no auth) ────────────────────────────────

def test_create_job_scraper(client):
    """Scraper POST /jobs creates a job; fans out a review to the test user."""
    with patch("app.routers.jobs._celery"):
        resp = client.post("/jobs", json=JOB_PAYLOAD)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Senior Python Engineer"


def test_dedup_by_external_id(client):
    """Scraper deduplicates jobs by external_id + source, returns 200 the second time."""
    payload = {**JOB_PAYLOAD, "external_id": "ext-001"}
    with patch("app.routers.jobs._celery"):
        r1 = client.post("/jobs", json=payload)
        r2 = client.post("/jobs", json=payload)
    assert r1.json()["id"] == r2.json()["id"]


# ── User-facing endpoints (auth required) ────────────────────

def test_list_jobs_empty(client):
    """Fresh user sees no reviews."""
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_list_jobs_after_scrape(client):
    """After scraper creates job, user sees 1 review in their list."""
    with patch("app.routers.jobs._celery"):
        client.post("/jobs", json=JOB_PAYLOAD)
    resp = client.get("/jobs")
    assert resp.json()["total"] == 1


def test_get_review(client):
    """GET /jobs/{review_id} returns the correct review."""
    with patch("app.routers.jobs._celery"):
        client.post("/jobs", json=JOB_PAYLOAD)
    review_id = client.get("/jobs").json()["items"][0]["id"]

    resp = client.get(f"/jobs/{review_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == review_id


def test_update_review_status(client):
    """PATCH /jobs/{review_id} updates status and stamps date_applied."""
    with patch("app.routers.jobs._celery"):
        client.post("/jobs", json=JOB_PAYLOAD)
    review_id = client.get("/jobs").json()["items"][0]["id"]

    resp = client.patch(f"/jobs/{review_id}", json={"status": "applied"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert resp.json()["date_applied"] is not None


def test_timeline_records_status_change(client):
    """Status change writes a timeline event."""
    with patch("app.routers.jobs._celery"):
        client.post("/jobs", json=JOB_PAYLOAD)
    review_id = client.get("/jobs").json()["items"][0]["id"]
    client.patch(f"/jobs/{review_id}", json={"status": "applied"})

    resp = client.get(f"/jobs/{review_id}/timeline")
    assert resp.status_code == 200
    event_types = [e["event_type"] for e in resp.json()]
    assert "status_change" in event_types


def test_filter_by_status(client):
    """?status= filter returns only matching reviews."""
    with patch("app.routers.jobs._celery"):
        client.post("/jobs", json=JOB_PAYLOAD)
        client.post("/jobs", json={**JOB_PAYLOAD, "url": "https://jobs.acme.com/456", "external_id": "ext-002"})

    items = client.get("/jobs?status=new").json()["items"]
    client.patch(f"/jobs/{items[0]['id']}", json={"status": "applied"})

    resp = client.get("/jobs?status=applied")
    assert resp.json()["total"] == 1


# ── Manual job endpoint (authenticated) ──────────────────────

def test_create_manual_job(client):
    """POST /jobs/manual creates a job + review for the current user."""
    with patch("app.routers.jobs._celery"):
        resp = client.post("/jobs/manual", json=JOB_PAYLOAD)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Senior Python Engineer"
    assert data["status"] == "new"


def test_manual_job_dedup_by_url(client):
    """Re-submitting the same URL returns the existing review (200)."""
    with patch("app.routers.jobs._celery"):
        r1 = client.post("/jobs/manual", json=JOB_PAYLOAD)
        r2 = client.post("/jobs/manual", json=JOB_PAYLOAD)
    assert r1.json()["id"] == r2.json()["id"]


# ── AI review endpoint (internal, no auth) ───────────────────

def test_ai_review(client):
    """POST /jobs/{job_id}/ai-review?user_id= updates the review with AI scores."""
    with patch("app.routers.jobs._celery"):
        client.post("/jobs", json=JOB_PAYLOAD)

    items = client.get("/jobs").json()["items"]
    job_id = items[0]["job_id"]

    review_payload = {
        "ai_score": 8.5,
        "ai_summary": "Strong match. Python required, remote available, salary in range.",
        "ai_pros": ["Remote OK", "Python focused", "Good salary"],
        "ai_cons": ["Startup, unproven stability"],
        "skills_rank": 9,
        "experience_rank": 7,
        "location_rank": 9,
        "education_rank": 6,
        "salary_rank": 8,
        "recommended": True,
    }
    resp = client.post(f"/jobs/{job_id}/ai-review?user_id={TEST_USER_ID}", json=review_payload)
    assert resp.status_code == 200
    assert resp.json()["ai_score"] == 8.5
    assert resp.json()["status"] == "reviewed"
    assert resp.json()["recommended"] is True
