"""Remembering permanent LLM key failures, so a user whose model was retired is
told instead of silently getting no scores.

The rule these tests exist to protect: only PERMANENT, user-fixable verdicts are
recorded. Recording a transient failure would tell a rate-limited user their model
is gone and send them to change a setting that was never wrong.
"""

from app import models
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID

STATUS_URL = f"/keys/internal/{TEST_USER_ID}/llm/status"
LLM_URL = f"/keys/internal/{TEST_USER_ID}/llm"


def _key(db, provider=models.LLMProvider.ANTHROPIC, model="claude-haiku-4-5"):
    row = models.UserAPIKey(
        user_id=TEST_USER_ID, provider=provider,
        encrypted_key=encrypt_api_key("sk-test"), preferred_model=model,
    )
    db.add(row)
    db.commit()
    return row


def _reload(db):
    return db.query(models.UserAPIKey).filter_by(user_id=TEST_USER_ID).first()


# ── the internal status endpoint ──────────────────────────────

def test_records_a_permanent_verdict(client, db):
    _key(db)
    r = client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "404 model not found"})
    assert r.status_code == 200

    key = _reload(db)
    assert key.last_error_kind == "invalid_model"
    assert "404" in key.last_error
    assert key.last_error_at is not None


def test_success_clears_a_recorded_verdict(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_key", "detail": "bad key"})
    r = client.post(STATUS_URL, json={"kind": None})
    assert r.status_code == 200

    key = _reload(db)
    assert key.last_error_kind is None
    assert key.last_error is None
    assert key.last_error_at is None


def test_rate_limited_is_recorded(client, db):
    """Not a rejection — the key and model are both fine. Recorded anyway,
    because a quota ceiling is the user's to act on and silence looks exactly
    like having nothing to score."""
    _key(db)
    r = client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429 RESOURCE_EXHAUSTED"})
    assert r.status_code == 200
    assert _reload(db).last_error_kind == "rate_limited"


def test_a_later_success_clears_rate_limited(client, db):
    """Throttling passes on its own, so the banner must not outlive it."""
    _key(db)
    client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429"})
    client.post(STATUS_URL, json={"kind": None})
    assert _reload(db).last_error_kind is None


def test_rate_limited_key_can_still_be_made_active(client, db):
    """Unlike a rejected key, a throttled one still works — refusing to select it
    would be telling the user to fix something that isn't broken."""
    _key(db)
    client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429"})
    assert client.put("/keys/active", json={"provider": "anthropic"}).status_code == 200


def test_unknown_kind_is_rejected(client, db):
    """The column is a small closed set. A typo'd or invented kind would render
    as a blank banner the user can't act on."""
    _key(db)
    assert client.post(STATUS_URL, json={"kind": "quota_exceeded"}).status_code == 400
    assert _reload(db).last_error_kind is None


def test_status_404s_without_a_key(client, db):
    assert client.post(STATUS_URL, json={"kind": "invalid_model"}).status_code == 404


def test_long_detail_is_truncated(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "x" * 5000})
    assert len(_reload(db).last_error) == 1000


# ── what the worker fetches ───────────────────────────────────

def test_internal_llm_409s_when_no_model_selected(client, db):
    """409, not 404 — 404 already means "no key at all" and the worker skips
    quietly on that. This is a different, user-fixable state."""
    _key(db, model=None)
    r = client.get(LLM_URL)
    assert r.status_code == 409
    assert "model" in r.json()["detail"].lower()


def test_internal_llm_404s_when_no_key(client, db):
    assert client.get(LLM_URL).status_code == 404


def test_internal_llm_reports_whether_an_error_is_recorded(client, db):
    """Lets the worker skip the clear-my-error post-back on the overwhelming
    majority of reviews, where there was never an error to clear."""
    _key(db)
    assert client.get(LLM_URL).json()["last_error_kind"] is None

    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "gone"})
    assert client.get(LLM_URL).json()["last_error_kind"] == "invalid_model"


# ── clearing on user action ───────────────────────────────────

def test_choosing_a_new_model_clears_the_error(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "gone"})
    client.patch("/keys/anthropic", json={"preferred_model": "claude-sonnet-4-6"})
    assert _reload(db).last_error_kind is None


def test_replacing_the_key_clears_the_error(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_key", "detail": "401"})
    client.put("/keys", json={"provider": "anthropic", "api_key": "sk-ant-new9999"})
    assert _reload(db).last_error_kind is None


def test_resaving_a_key_does_not_wipe_the_model(client, db):
    """Regression: the Settings form re-saves a key without the model field, and
    `payload.preferred_model or None` used to null it. With no default model to
    fall back on, that silently broke every AI feature for the user."""
    _key(db, model="claude-haiku-4-5")
    client.put("/keys", json={"provider": "anthropic", "api_key": "sk-ant-rotated"})
    assert _reload(db).preferred_model == "claude-haiku-4-5"


def test_explicit_null_still_clears_the_model(client, db):
    """Sending the field explicitly is a deliberate clear, not an omission."""
    _key(db, model="claude-haiku-4-5")
    client.put("/keys", json={
        "provider": "anthropic", "api_key": "sk-ant-rotated", "preferred_model": None,
    })
    assert _reload(db).preferred_model is None


# ── what the user sees ────────────────────────────────────────

def test_list_keys_exposes_the_error(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "model gone"})
    row = next(k for k in client.get("/keys").json() if k["provider"] == "anthropic")
    assert row["last_error_kind"] == "invalid_model"
    assert row["last_error"] == "model gone"
