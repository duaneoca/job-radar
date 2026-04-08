"""Tests for the /criteria endpoints."""


CRITERIA_PAYLOAD = {
    "name": "Senior Python roles",
    "job_titles": ["Senior Python Engineer", "Staff Engineer", "Backend Engineer"],
    "required_skills": ["Python", "Docker"],
    "preferred_skills": ["Kubernetes", "FastAPI"],
    "remote_only": True,
    "min_salary": 120000,
}


def test_create_criteria(client):
    resp = client.post("/criteria", json=CRITERIA_PAYLOAD)
    assert resp.status_code == 201
    assert resp.json()["name"] == "Senior Python roles"


def test_list_criteria(client):
    client.post("/criteria", json=CRITERIA_PAYLOAD)
    resp = client.get("/criteria")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_activate_criteria(client):
    id1 = client.post("/criteria", json=CRITERIA_PAYLOAD).json()["id"]
    id2 = client.post("/criteria", json={**CRITERIA_PAYLOAD, "name": "second"}).json()["id"]

    client.post(f"/criteria/{id2}/activate")
    active = client.get("/criteria/active").json()
    assert active["id"] == id2


def test_update_criteria(client):
    cid = client.post("/criteria", json=CRITERIA_PAYLOAD).json()["id"]
    resp = client.put(f"/criteria/{cid}", json={**CRITERIA_PAYLOAD, "min_salary": 150000})
    assert resp.json()["min_salary"] == 150000
