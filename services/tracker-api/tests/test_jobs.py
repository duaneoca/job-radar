"""Tests for the /jobs endpoints."""

import pytest


JOB_PAYLOAD = {
    "title": "Senior Python Engineer",
    "company": "Acme Corp",
    "url": "https://jobs.acme.com/123",
    "source": "indeed",
    "location": "Austin, TX",
    "remote": True,
    "salary_min": 130000,
    "salary_max": 160000,
}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_job(client):
    resp = client.post("/jobs", json=JOB_PAYLOAD)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Senior Python Engineer"
    assert data["status"] == "new"


def test_list_jobs_empty(client):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_list_jobs(client):
    client.post("/jobs", json=JOB_PAYLOAD)
    resp = client.get("/jobs")
    assert resp.json()["total"] == 1


def test_get_job(client):
    create_resp = client.post("/jobs", json=JOB_PAYLOAD)
    job_id = create_resp.json()["id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_update_job_status(client):
    create_resp = client.post("/jobs", json=JOB_PAYLOAD)
    job_id = create_resp.json()["id"]

    resp = client.patch(f"/jobs/{job_id}", json={"status": "applied"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert resp.json()["date_applied"] is not None


def test_timeline_records_status_change(client):
    create_resp = client.post("/jobs", json=JOB_PAYLOAD)
    job_id = create_resp.json()["id"]
    client.patch(f"/jobs/{job_id}", json={"status": "applied"})

    resp = client.get(f"/jobs/{job_id}/timeline")
    assert resp.status_code == 200
    events = resp.json()
    event_types = [e["event_type"] for e in events]
    assert "status_change" in event_types


def test_dedup_by_external_id(client):
    payload = {**JOB_PAYLOAD, "external_id": "ext-001"}
    resp1 = client.post("/jobs", json=payload)
    resp2 = client.post("/jobs", json=payload)
    assert resp1.json()["id"] == resp2.json()["id"]


def test_filter_by_status(client):
    client.post("/jobs", json=JOB_PAYLOAD)
    job_id = client.post("/jobs", json={**JOB_PAYLOAD, "url": "https://jobs.acme.com/456", "external_id": "ext-002"}).json()["id"]
    client.patch(f"/jobs/{job_id}", json={"status": "applied"})

    resp = client.get("/jobs?status=applied")
    assert resp.json()["total"] == 1


def test_delete_job(client):
    job_id = client.post("/jobs", json=JOB_PAYLOAD).json()["id"]
    del_resp = client.delete(f"/jobs/{job_id}")
    assert del_resp.status_code == 204

    get_resp = client.get(f"/jobs/{job_id}")
    assert get_resp.status_code == 404


def test_ai_review(client):
    job_id = client.post("/jobs", json=JOB_PAYLOAD).json()["id"]

    review_payload = {
        "ai_score": 8.5,
        "ai_summary": "Strong match. Python required, remote available, salary in range.",
        "ai_pros": ["Remote OK", "Python focused", "Good salary"],
        "ai_cons": ["Startup, unproven stability"],
    }
    resp = client.post(f"/jobs/{job_id}/ai-review", json=review_payload)
    assert resp.status_code == 200
    assert resp.json()["ai_score"] == 8.5
    assert resp.json()["status"] == "reviewed"
