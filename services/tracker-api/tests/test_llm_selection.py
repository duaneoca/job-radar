"""Active-LLM-key selection: explicit choice (users.selected_llm_provider) with
priority-order fallback, shared by scoring / research / the email agent."""

from app import models
from app.llm import get_active_llm_key
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID


def _add_key(db, provider, model=None):
    db.add(models.UserAPIKey(
        user_id=TEST_USER_ID, provider=provider,
        encrypted_key=encrypt_api_key(f"key-{provider.value}"),
        preferred_model=model,
    ))
    db.commit()


def _select(db, provider):
    user = db.query(models.User).filter_by(id=TEST_USER_ID).first()
    user.selected_llm_provider = provider
    db.commit()


# ── get_active_llm_key ────────────────────────────────────────

def test_none_when_no_keys(db, test_user):
    assert get_active_llm_key(TEST_USER_ID, db) is None


def test_priority_fallback_no_selection(db, test_user):
    # Has Google + Groq, no Anthropic/OpenAI, no explicit selection → Google (priority).
    _add_key(db, models.LLMProvider.GROQ)
    _add_key(db, models.LLMProvider.GOOGLE)
    assert get_active_llm_key(TEST_USER_ID, db).provider == models.LLMProvider.GOOGLE


def test_priority_prefers_anthropic(db, test_user):
    _add_key(db, models.LLMProvider.OPENAI)
    _add_key(db, models.LLMProvider.ANTHROPIC)
    assert get_active_llm_key(TEST_USER_ID, db).provider == models.LLMProvider.ANTHROPIC


def test_explicit_selection_wins_over_priority(db, test_user):
    _add_key(db, models.LLMProvider.ANTHROPIC)
    _add_key(db, models.LLMProvider.GROQ)
    _select(db, models.LLMProvider.GROQ)   # pick the lower-priority one on purpose
    assert get_active_llm_key(TEST_USER_ID, db).provider == models.LLMProvider.GROQ


def test_selection_without_key_falls_back(db, test_user):
    # Selected Anthropic but only have OpenAI → fall back to priority (OpenAI).
    _add_key(db, models.LLMProvider.OPENAI)
    _select(db, models.LLMProvider.ANTHROPIC)
    assert get_active_llm_key(TEST_USER_ID, db).provider == models.LLMProvider.OPENAI


# ── PUT /keys/active + list active flag ───────────────────────

def test_list_marks_effective_active(client, db):
    _add_key(db, models.LLMProvider.ANTHROPIC)
    _add_key(db, models.LLMProvider.GOOGLE)
    body = client.get("/keys").json()
    active = [k["provider"] for k in body if k["active"]]
    assert active == ["anthropic"]   # priority default


def test_set_active_switches(client, db):
    _add_key(db, models.LLMProvider.ANTHROPIC)
    _add_key(db, models.LLMProvider.GOOGLE)
    r = client.put("/keys/active", json={"provider": "google"})
    assert r.status_code == 200
    active = [k["provider"] for k in r.json() if k["active"]]
    assert active == ["google"]


def test_set_active_clear_reverts_to_priority(client, db):
    _add_key(db, models.LLMProvider.ANTHROPIC)
    _add_key(db, models.LLMProvider.GOOGLE)
    client.put("/keys/active", json={"provider": "google"})
    r = client.put("/keys/active", json={"provider": None})
    assert r.status_code == 200
    active = [k["provider"] for k in r.json() if k["active"]]
    assert active == ["anthropic"]


def test_set_active_without_key_404(client, db):
    _add_key(db, models.LLMProvider.GOOGLE)
    assert client.put("/keys/active", json={"provider": "anthropic"}).status_code == 404


def test_set_active_rejects_non_llm(client, db):
    _add_key(db, models.LLMProvider.ADZUNA)
    assert client.put("/keys/active", json={"provider": "adzuna"}).status_code == 400
