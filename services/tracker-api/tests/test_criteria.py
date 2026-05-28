"""Tests for the /criteria endpoints — per-user upsert API."""

CRITERIA_PAYLOAD = {
    "name": "Senior Python roles",
    "job_titles": ["Senior Python Engineer", "Staff Engineer", "Backend Engineer"],
    "required_skills": ["Python", "Docker"],
    "preferred_skills": ["Kubernetes", "FastAPI"],
    "remote_only": True,
    "min_salary": 120000,
}


def test_get_criteria_empty(client):
    """404 when the user has no criteria yet."""
    resp = client.get("/criteria")
    assert resp.status_code == 404


def test_upsert_criteria_creates(client):
    """PUT /criteria creates criteria when none exists."""
    resp = client.put("/criteria", json=CRITERIA_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Senior Python roles"
    assert data["min_salary"] == 120000


def test_upsert_criteria_updates(client):
    """Second PUT updates the existing criteria in-place."""
    client.put("/criteria", json=CRITERIA_PAYLOAD)
    resp = client.put("/criteria", json={**CRITERIA_PAYLOAD, "min_salary": 150000})
    assert resp.status_code == 200
    assert resp.json()["min_salary"] == 150000


def test_get_criteria_after_upsert(client):
    """GET /criteria returns the active criteria after upsert."""
    client.put("/criteria", json=CRITERIA_PAYLOAD)
    resp = client.get("/criteria")
    assert resp.status_code == 200
    assert resp.json()["min_salary"] == 120000
    assert resp.json()["name"] == "Senior Python roles"
