"""What a connected AI assistant may do, and how much — enforced in tracker-api
so it holds for any client holding a bearer token (app/assistant.py).

Runs the real get_current_user: a cookie session for the user's own actions,
an OAuth bearer for the assistant's."""

from datetime import timedelta
from unittest.mock import patch

import pytest

from app import assistant, models
from app.routers import generate as generate_mod

from .conftest import TEST_USER_ID
from .test_oauth import _bearer, _consent, _fresh_ratelimit, api, web  # noqa: F401 — fixtures (+ autouse limiter reset)


def _job(client, n=1):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", headers={"X-Internal-Token": "test-internal-token"},
                    json={"title": f"Eng {n}", "company": f"Acme {n}", "url": f"https://x/{n}", "source": "manual"})


@pytest.fixture
def app_h(web, api):  # noqa: F811
    """Bearer headers for a connected Claude with every scope."""
    tokens, _, _ = _consent(web, api)
    return _bearer(tokens)


def _rid(api, h):  # noqa: F811
    return api.get("/jobs", headers=h).json()["items"][0]["id"]


# ── PATCH: star, expire, dismiss — nothing else ───────────────

def test_assistant_can_star_and_the_timeline_says_who(api, app_h):  # noqa: F811
    _job(api)
    rid = _rid(api, app_h)
    r = api.patch(f"/jobs/{rid}", headers=app_h, json={"starred": True})
    assert r.status_code == 200 and r.json()["starred"] is True
    events = [e["description"] for e in api.get(f"/jobs/{rid}/timeline", headers=app_h).json()]
    assert "Starred by Claude (Anthropic)" in events


@pytest.mark.parametrize("status_", ["expired", "dismissed"])
def test_assistant_can_expire_or_dismiss_with_a_reason(api, app_h, status_):  # noqa: F811
    _job(api)
    rid = _rid(api, app_h)
    r = api.patch(f"/jobs/{rid}", headers=app_h, json={"status": status_, "status_reason": "Posting is closed"})
    assert r.status_code == 200 and r.json()["status"] == status_
    events = [e["description"] for e in api.get(f"/jobs/{rid}/timeline", headers=app_h).json()]
    assert any(e.endswith(f"→ {status_} — by Claude (Anthropic): Posting is closed") for e in events)


def test_a_reason_is_required(api, app_h):  # noqa: F811
    _job(api)
    rid = _rid(api, app_h)
    assert api.patch(f"/jobs/{rid}", headers=app_h, json={"status": "expired"}).status_code == 422


@pytest.mark.parametrize("body", [
    {"status": "applied", "status_reason": "x"},
    {"status": "interviewing", "status_reason": "x"},
    {"status": "new", "status_reason": "x"},
    {"notes": "wiped"},
    {"date_applied": "2026-01-01T00:00:00Z"},
    {"application_answers": {"0": "hi"}},
    {"has_contact": True},
])
def test_everything_else_is_refused(api, app_h, body):  # noqa: F811
    _job(api)
    rid = _rid(api, app_h)
    assert api.patch(f"/jobs/{rid}", headers=app_h, json=body).status_code == 403
    job = api.get(f"/jobs/{rid}", headers=app_h).json()
    assert job["status"] == "new" and job["notes"] is None


def test_the_users_own_clicks_are_unrestricted(web):  # noqa: F811
    _job(web)
    rid = web.get("/jobs").json()["items"][0]["id"]
    assert web.patch(f"/jobs/{rid}", json={"status": "applied"}).json()["status"] == "applied"
    assert web.patch(f"/jobs/{rid}", json={"notes": "mine"}).json()["notes"] == "mine"


# ── notes: append-only ────────────────────────────────────────

def test_notes_are_appended_never_replaced(web, api, app_h):  # noqa: F811
    _job(web)
    rid = web.get("/jobs").json()["items"][0]["id"]
    web.patch(f"/jobs/{rid}", json={"notes": "My own thoughts."})
    r = api.post(f"/jobs/{rid}/notes", headers=app_h, json={"text": "Strong match on agents."})
    notes = r.json()["notes"]
    assert notes.startswith("My own thoughts.\n\n[")
    assert "· Claude (Anthropic)] Strong match on agents." in notes
    events = [e["description"] for e in api.get(f"/jobs/{rid}/timeline", headers=app_h).json()]
    assert "Note added by Claude (Anthropic)" in events


def test_note_length_is_capped(api, app_h):  # noqa: F811
    _job(api)
    rid = _rid(api, app_h)
    assert api.post(f"/jobs/{rid}/notes", headers=app_h, json={"text": "x" * 2001}).status_code == 422


# ── daily limits ──────────────────────────────────────────────

def test_expire_and_dismiss_share_one_daily_limit(api, app_h, monkeypatch):  # noqa: F811
    monkeypatch.setitem(assistant.LIMITS, assistant.ACTION_REMOVE, 2)
    for n in range(3):
        _job(api, n)
    ids = [i["id"] for i in api.get("/jobs", headers=app_h).json()["items"]]
    assert api.patch(f"/jobs/{ids[0]}", headers=app_h, json={"status": "expired", "status_reason": "r"}).status_code == 200
    assert api.patch(f"/jobs/{ids[1]}", headers=app_h, json={"status": "dismissed", "status_reason": "r"}).status_code == 200
    r = api.patch(f"/jobs/{ids[2]}", headers=app_h, json={"status": "expired", "status_reason": "r"})
    assert r.status_code == 429 and "Daily limit reached" in r.json()["detail"]
    assert api.get(f"/jobs/{ids[2]}", headers=app_h).json()["status"] == "new"


def test_the_limit_is_a_rolling_24_hours(db, api, app_h, monkeypatch):  # noqa: F811
    monkeypatch.setitem(assistant.LIMITS, assistant.ACTION_REMOVE, 1)
    for n in range(2):
        _job(api, n)
    ids = [i["id"] for i in api.get("/jobs", headers=app_h).json()["items"]]
    api.patch(f"/jobs/{ids[0]}", headers=app_h, json={"status": "expired", "status_reason": "r"})
    row = db.query(models.AssistantAction).one()
    row.created_at = row.created_at - timedelta(hours=25)
    db.commit()
    assert api.patch(f"/jobs/{ids[1]}", headers=app_h, json={"status": "expired", "status_reason": "r"}).status_code == 200


def _key(db):
    from app.security import encrypt_api_key
    db.add(models.UserAPIKey(user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
                             encrypted_key=encrypt_api_key("sk-test"), preferred_model="claude-haiku-4-5"))
    db.commit()


def test_research_limit_counts_new_runs_only(db, api, app_h, monkeypatch):  # noqa: F811
    _key(db)
    monkeypatch.setitem(assistant.LIMITS, assistant.ACTION_RESEARCH, 1)
    monkeypatch.setattr(generate_mod.generation_jobs, "run", lambda job_id: None)   # stays queued
    for n in range(2):
        _job(api, n)
    ids = [i["id"] for i in api.get("/jobs", headers=app_h).json()["items"]]
    assert api.post(f"/jobs/{ids[0]}/research", headers=app_h).status_code == 202
    # Asking again for the same running job re-attaches — not a new run, not counted.
    assert api.post(f"/jobs/{ids[0]}/research", headers=app_h).status_code == 202
    assert assistant.used(db, TEST_USER_ID, assistant.ACTION_RESEARCH) == 1
    db.query(models.GenerationJob).update({"status": models.GENERATION_DONE})
    db.commit()
    r = api.post(f"/jobs/{ids[1]}/research", headers=app_h)
    assert r.status_code == 429 and "research" in r.json()["detail"]


def test_the_users_own_runs_are_never_counted(db, web, monkeypatch):  # noqa: F811
    _key(db)
    monkeypatch.setitem(assistant.LIMITS, assistant.ACTION_RESEARCH, 0)
    monkeypatch.setattr(generate_mod.generation_jobs, "run", lambda job_id: None)
    _job(web)
    rid = web.get("/jobs").json()["items"][0]["id"]
    assert web.post(f"/jobs/{rid}/research").status_code == 202
    assert db.query(models.AssistantAction).count() == 0


def test_usage_endpoint_for_the_connected_apps_page(web, api, app_h):  # noqa: F811
    _job(api)
    rid = _rid(api, app_h)
    api.patch(f"/jobs/{rid}", headers=app_h, json={"status": "dismissed", "status_reason": "not for me"})
    u = web.get("/oauth/assistant-usage").json()
    assert u["remove"] == {"used": 1, "limit": 25}
    assert u["research"]["limit"] == 20 and u["tailor"]["limit"] == 10
    assert api.get("/oauth/assistant-usage", headers=app_h).status_code == 403   # cookie only
