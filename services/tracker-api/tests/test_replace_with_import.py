"""Find on company site → import → "this is that job" (POST /jobs/manual?replaces=).

The user clicks a job's dead Adzuna link, finds the posting on the company's own
site, and imports it from there. Adzuna's company names rarely match the careers
page's, so the automatic same-role match would miss it: the user says which job
it is, and that entry moves to the live posting."""

import uuid
from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID
from .test_oauth import _bearer, _consent, _fresh_ratelimit, api, web  # noqa: F401 — fixtures

ADZUNA = {"title": "Sr. Platform Engineer", "company": "NVIDIA", "location": "Santa Clara, CA",
          "description": "An excerpt that stops mid-sen…", "url": "https://www.adzuna.com/land/ad/1",
          "source": "adzuna", "external_id": "az-1"}
COMPANY_PAGE = {"title": "Senior Platform Engineer", "company": "NVIDIA Corporation",
                "location": "US, CA, Santa Clara", "description": "The full posting. " * 50,
                "url": "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/123",
                "source": "manual", "salary_min": 180000, "salary_max": 250000}


def _scrape(client, **over):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json={**ADZUNA, **over})
    url = over.get("url", ADZUNA["url"])
    return next(j for j in client.get("/jobs").json()["items"] if j["url"] == url)


def _replace(client, review_id, **over):
    with patch("app.routers.jobs._celery") as celery:
        r = client.post(f"/jobs/manual?replaces={review_id}", json={**COMPANY_PAGE, **over})
    return r, celery


def test_the_entry_moves_to_the_company_posting_and_is_rescored(client, db):
    old = _scrape(client)
    old_job_id = old["job_id"]
    r, celery = _replace(client, old["id"])
    assert r.status_code == 200 and r.json()["already_had"] == "replaced"
    out = r.json()
    assert out["id"] == old["id"]                                   # the same entry…
    assert out["job_id"] != old_job_id                              # …on a new row of its own
    assert out["url"] == COMPANY_PAGE["url"] and out["company"] == "NVIDIA Corporation"
    assert (out["salary_min"], out["salary_max"]) == (180000, 250000)
    full = client.get(f"/jobs/{old['id']}").json()
    assert full["description"].startswith("The full posting.")
    assert "Link and posting updated from the company's site" in [e["description"] for e in full["timeline"]]
    celery.send_task.assert_called_once_with("app.tasks.review_job", args=[out["job_id"], str(TEST_USER_ID)], queue="review")
    # The shared Adzuna row itself is untouched — another user may hold it.
    assert db.get(models.Job, uuid.UUID(old_job_id)).url == ADZUNA["url"]
    assert client.get("/jobs").json()["total"] == 1


def test_adzunas_next_relisting_is_still_recognised(client, db):
    old = _scrape(client)
    _replace(client, old["id"])
    # Adzuna re-posts the same ad under a new id a few days later.
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}",
                    json={**ADZUNA, "external_id": "az-2", "url": "https://www.adzuna.com/land/ad/2"})
    items = client.get("/jobs").json()["items"]
    assert len(items) == 1 and items[0]["url"] == COMPANY_PAGE["url"]


def test_an_expired_job_comes_back(client, db):
    old = _scrape(client)
    client.patch(f"/jobs/{old['id']}", json={"status": "expired"})
    r, _ = _replace(client, old["id"])
    assert r.json()["status"] == "new"
    events = [e["description"] for e in client.get(f"/jobs/{old['id']}").json()["timeline"]]
    assert any("expired → new — found live on the company's site" in e for e in events)


def test_other_statuses_are_left_alone(client, db):
    old = _scrape(client)
    client.patch(f"/jobs/{old['id']}", json={"status": "dismissed"})
    assert _replace(client, old["id"])[0].json()["status"] == "dismissed"


def test_a_link_is_required(client, db):
    old = _scrape(client)
    r, celery = _replace(client, old["id"], url="")
    assert r.status_code == 422 and not celery.send_task.called


def test_someone_elses_job_is_a_404(client, db):
    r, celery = _replace(client, uuid.uuid4())
    assert r.status_code == 404 and not celery.send_task.called


def test_a_page_already_held_returns_that_entry_and_changes_nothing(client, db):
    with patch("app.routers.jobs._celery"):
        held = client.post("/jobs/manual", json=COMPANY_PAGE).json()
    old = _scrape(client, title="Something else entirely", company="Other Co")
    r, celery = _replace(client, old["id"])
    assert r.status_code == 200 and r.json()["already_had"] == "same_link" and r.json()["id"] == held["id"]
    assert client.get(f"/jobs/{old['id']}").json()["url"] == ADZUNA["url"]
    assert not celery.send_task.called


def test_without_replaces_import_is_unchanged(client, db):
    _scrape(client)
    with patch("app.routers.jobs._celery"):
        r = client.post("/jobs/manual", json=COMPANY_PAGE)
    # Names differ, so no automatic match: a second job, as before this change.
    assert r.status_code == 201 and client.get("/jobs").json()["total"] == 2


def test_a_connected_assistant_cannot_import_or_replace(web, api):  # noqa: F811
    tokens, _, _ = _consent(web, api)
    old = _scrape(web)
    with patch("app.routers.jobs._celery"):
        r = api.post(f"/jobs/manual?replaces={old['id']}", headers=_bearer(tokens), json=COMPANY_PAGE)
    assert r.status_code == 403
