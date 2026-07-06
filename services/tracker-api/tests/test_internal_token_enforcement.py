"""Phase 2 (security #2): internal service-to-service endpoints require the
shared X-Internal-Token. The `client` fixture sends a valid token by default;
these tests pop it to prove each endpoint rejects unauthenticated callers, and
confirm a valid token is accepted."""

from unittest.mock import patch

from .conftest import TEST_USER_ID

JOB = {
    "title": "Forward Deployed Engineer",
    "company": "Acme",
    "url": "https://jobs.acme.com/1",
    "source": "adzuna",
    "external_id": "adz-1",
}


def _drop_token(client):
    client.headers.pop("X-Internal-Token", None)
    return client


# ── rejection without a token ────────────────────────────────

def test_post_jobs_requires_token(client):
    _drop_token(client)
    assert client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB).status_code == 401


def test_keys_internal_requires_token(client):
    _drop_token(client)
    assert client.get(f"/keys/internal/{TEST_USER_ID}/llm").status_code == 401


def test_scraper_configs_requires_token(client):
    _drop_token(client)
    assert client.get("/criteria/scraper/user-configs").status_code == 401


def test_admin_internal_cleanup_requires_token(client):
    _drop_token(client)
    assert client.post("/admin/internal/cleanup").status_code == 401


def test_bad_token_rejected(client):
    client.headers.update({"X-Internal-Token": "not-the-real-token"})
    assert client.get("/criteria/scraper/user-configs").status_code == 401


# ── acceptance with the valid default token ──────────────────

def test_post_jobs_accepts_valid_token(client):
    with patch("app.routers.jobs._celery"):  # don't hit the real broker
        r = client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    assert r.status_code == 201, r.text


def test_scraper_configs_accepts_valid_token(client):
    assert client.get("/criteria/scraper/user-configs").status_code == 200
