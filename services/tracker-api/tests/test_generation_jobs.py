"""Research and résumé tailoring run in the background.

POST returns 202 with the GenerationJob as it was when the response was built —
status "queued" — and the work happens after the response. Starlette's
TestClient runs background tasks before returning, so by the time a test reads
the row back it has finished; tests that need the in-between states stop the
runner with `no_run`.
"""

import logging
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app import generation_jobs, models, resume_tailor
from app.routers import generate as generate_mod
from app.routers.admin import _do_cleanup

from .conftest import TEST_USER_ID
from .test_resume_tailor import NOTES, TAILORED, _s, _seed, _scrape


@pytest.fixture
def no_run(monkeypatch):
    """Record the job but never execute it — to look at queued/running rows."""
    monkeypatch.setattr(generation_jobs, "run", lambda job_id: None)


def _result(client, rid, kind):
    """The run as it stands now — what a page sees when it polls."""
    return client.get(f"/jobs/{rid}/generation").json()[kind]


def _fake_llm(monkeypatch, text="Acme builds widgets."):
    monkeypatch.setattr(generate_mod, "llm_complete",
                        lambda system, messages, api_key, model, max_tokens=1024, **kw: text)


# ── the happy path ────────────────────────────────────────────

def test_research_returns_202_and_lands_on_the_job(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    _fake_llm(monkeypatch)

    r = client.post(f"/jobs/{rid}/research")
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["kind"] == "research" and job["status"] == "queued" and job["error"] is None

    assert client.get(f"/jobs/{rid}").json()["research_summary"] == "Acme builds widgets."
    status = client.get(f"/jobs/{rid}/generation").json()
    assert status["research"]["id"] == job["id"] and status["research"]["status"] == "done"
    assert status["tailor"] is None
    assert client.get(f"/jobs/{rid}/generation/{job['id']}").json()["status"] == "done"


def test_tailor_returns_202_and_the_draft_appears(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))

    r = client.post(f"/jobs/{rid}/tailor-resume")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    assert _result(client, rid, "tailor")["status"] == "done"
    state = client.get(f"/jobs/{rid}/tailor-resume").json()
    assert len(state["changes"]) == 3 and state["status"] == "draft"


def test_finished_run_leaves_a_timeline_entry(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    _fake_llm(monkeypatch)
    client.post(f"/jobs/{rid}/research")
    events = {e["event_type"]: e["description"] for e in client.get(f"/jobs/{rid}/timeline").json()}
    assert events["research_generated"] == "Company research generated"


def test_a_non_web_source_is_named_in_the_timeline(db, test_user, monkeypatch):
    """The MCP server will ask on the user's behalf; the timeline must say so."""
    _seed(db)
    job_row = models.Job(id=uuid.uuid4(), title="T", company="C", url="https://x/1", source="manual")
    db.add(job_row)
    db.flush()
    review = models.UserJobReview(id=uuid.uuid4(), user_id=TEST_USER_ID, job_id=job_row.id)
    db.add(review)
    db.commit()
    monkeypatch.setitem(generation_jobs.RUNNERS, "research", lambda db_, u, r: None)

    job, created = generation_jobs.start(db, test_user, review, "research", source="mcp")
    assert created
    generation_jobs.run(job.id)
    db.expire_all()
    ev = db.query(models.TimelineEvent).filter_by(review_id=review.id, event_type="research_generated").one()
    assert ev.description == "Company research generated via mcp"


# ── preflight: the user hears about their own problems now ────

def test_research_without_a_key_fails_in_the_request(client, db):
    db.add(models.Profile(user_id=TEST_USER_ID, name="default", is_active=True, resume_text="x"))
    db.commit()
    rid = _scrape(client)
    r = client.post(f"/jobs/{rid}/research")
    assert r.status_code == 400
    assert db.query(models.GenerationJob).count() == 0


def test_tailor_without_a_resume_fails_in_the_request(client, db):
    _seed(db)
    db.query(models.Profile).update({"resume_text": ""})
    db.commit()
    rid = _scrape(client)
    assert client.post(f"/jobs/{rid}/tailor-resume").status_code == 400
    assert db.query(models.GenerationJob).count() == 0


# ── one active run per user per kind ──────────────────────────

def test_second_request_for_the_same_job_attaches(client, db, no_run):
    _seed(db)
    rid = _scrape(client)
    first = client.post(f"/jobs/{rid}/research").json()
    assert first["status"] == "queued"
    second = client.post(f"/jobs/{rid}/research").json()
    assert second["id"] == first["id"]
    assert db.query(models.GenerationJob).count() == 1


def test_a_run_for_another_job_is_refused_while_one_is_active(client, db, no_run):
    _seed(db)
    rid1 = _scrape(client)
    from unittest.mock import patch
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}",
                    json={"title": "Other", "company": "Globex", "url": "https://x/2", "source": "manual"})
    rid2 = next(i["id"] for i in client.get("/jobs").json()["items"] if i["id"] != rid1)

    assert client.post(f"/jobs/{rid1}/tailor-resume").status_code == 202
    r = client.post(f"/jobs/{rid2}/tailor-resume")
    assert r.status_code == 409
    assert "already running" in r.json()["detail"]
    # Different kind is independent.
    assert client.post(f"/jobs/{rid2}/research").status_code == 202


# ── failure is recorded, not raised ───────────────────────────

def test_llm_failure_is_recorded_on_the_job_at_warning(client, db, monkeypatch, caplog):
    _seed(db)
    rid = _scrape(client)

    def boom(*a, **k):
        raise HTTPException(status_code=400, detail="Invalid API key. Check Settings → API Keys.")
    monkeypatch.setattr(generate_mod, "llm_complete", boom)

    with caplog.at_level(logging.WARNING, logger="app.generation_jobs"):
        client.post(f"/jobs/{rid}/research")
    job = _result(client, rid, "research")
    assert job["status"] == "failed"
    assert job["error"] == "Invalid API key. Check Settings → API Keys."
    assert client.get(f"/jobs/{rid}").json()["research_summary"] is None
    levels = {r.levelno for r in caplog.records if r.name == "app.generation_jobs"}
    assert levels == {logging.WARNING}          # the user's problem: never in the error digest


def test_unexpected_crash_is_recorded_and_logged_at_error(client, db, monkeypatch, caplog):
    _seed(db)
    rid = _scrape(client)

    def boom(*a, **k):
        raise RuntimeError("our bug")
    monkeypatch.setattr(generate_mod, "llm_complete", boom)

    with caplog.at_level(logging.ERROR, logger="app.generation_jobs"):
        client.post(f"/jobs/{rid}/research")
    job = _result(client, rid, "research")
    assert job["status"] == "failed"
    assert job["error"] == generation_jobs.UNEXPECTED_ERROR
    assert "our bug" not in job["error"]
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_a_failed_run_does_not_block_the_next_one(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)

    def boom(*a, **k):
        raise HTTPException(status_code=429, detail="rate limited")
    monkeypatch.setattr(generate_mod, "llm_complete", boom)
    client.post(f"/jobs/{rid}/research")
    assert _result(client, rid, "research")["status"] == "failed"

    _fake_llm(monkeypatch)
    client.post(f"/jobs/{rid}/research")
    assert _result(client, rid, "research")["status"] == "done"


# ── a run the process died under ──────────────────────────────

def test_stale_running_job_is_failed_on_read(client, db, no_run):
    _seed(db)
    rid = _scrape(client)
    job_id = client.post(f"/jobs/{rid}/tailor-resume").json()["id"]
    row = db.get(models.GenerationJob, uuid.UUID(job_id))
    row.status = models.GENERATION_RUNNING
    row.started_at = generation_jobs._utcnow() - generation_jobs.STALE_AFTER - timedelta(seconds=1)
    db.commit()

    got = client.get(f"/jobs/{rid}/generation").json()["tailor"]
    assert got["status"] == "failed"
    assert got["error"] == generation_jobs.STALE_ERROR
    # …and the user can start again.
    assert client.post(f"/jobs/{rid}/tailor-resume").status_code == 202


def test_a_run_within_the_window_is_left_alone(client, db, no_run):
    _seed(db)
    rid = _scrape(client)
    client.post(f"/jobs/{rid}/research")
    assert client.get(f"/jobs/{rid}/generation").json()["research"]["status"] == "queued"


# ── ownership ─────────────────────────────────────────────────

def test_another_users_job_is_not_visible(client, db, no_run):
    _seed(db)
    rid = _scrape(client)
    job_id = client.post(f"/jobs/{rid}/research").json()["id"]
    other = models.User(id=uuid.uuid4(), email="o@example.com", password_hash="x", is_approved=True)
    db.add(other)
    db.commit()
    db.query(models.GenerationJob).update({"user_id": other.id})
    db.commit()
    assert client.get(f"/jobs/{rid}/generation/{job_id}").status_code == 404
    assert client.get(f"/jobs/{rid}/generation").json()["research"] is None


def test_job_id_must_belong_to_that_review(client, db, no_run):
    _seed(db)
    rid = _scrape(client)
    from unittest.mock import patch
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}",
                    json={"title": "Other", "company": "Globex", "url": "https://x/2", "source": "manual"})
    rid2 = next(i["id"] for i in client.get("/jobs").json()["items"] if i["id"] != rid)
    job_id = client.post(f"/jobs/{rid}/research").json()["id"]
    assert client.get(f"/jobs/{rid2}/generation/{job_id}").status_code == 404


# ── nightly sweep ─────────────────────────────────────────────

def test_cleanup_sweeps_generation_rows(client, db, monkeypatch, no_run):
    _seed(db)
    rid = _scrape(client)
    old_done = client.post(f"/jobs/{rid}/research").json()["id"]
    row = db.get(models.GenerationJob, uuid.UUID(old_done))
    row.status = models.GENERATION_DONE
    row.finished_at = generation_jobs._utcnow() - timedelta(days=60)
    stale = models.GenerationJob(user_id=TEST_USER_ID, review_id=row.review_id, kind="tailor",
                                 status=models.GENERATION_RUNNING,
                                 started_at=generation_jobs._utcnow() - timedelta(hours=1))
    db.add(stale)
    db.commit()

    result = _do_cleanup(db)

    assert result["generation_jobs_deleted"] == 1
    assert result["generation_jobs_failed_stale"] == 1
    db.expire_all()
    assert db.get(models.GenerationJob, stale.id).status == models.GENERATION_FAILED


def test_deleting_the_review_removes_its_runs(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    _fake_llm(monkeypatch)
    client.post(f"/jobs/{rid}/research")
    assert db.query(models.GenerationJob).count() == 1
    assert client.delete(f"/jobs/{rid}").status_code == 204
    assert db.query(models.GenerationJob).count() == 0
