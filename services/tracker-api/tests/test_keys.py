"""Tests for the API keys router — including Adzuna's two-part credential
and the rule that non-LLM providers (adzuna/tavily) are never used as the LLM key."""

import pytest
from fastapi import HTTPException


def test_llm_key_upsert_unchanged(client):
    """Single-string LLM keys still work and hint shows last 4."""
    resp = client.put("/keys", json={"provider": "anthropic", "api_key": "sk-ant-test1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["key_hint"].endswith("1234")


def test_adzuna_upsert_packs_two_parts(client):
    """Adzuna takes app_id + app_key; hint is the app_key's last 4."""
    resp = client.put(
        "/keys",
        json={"provider": "adzuna", "app_id": "12345678", "app_key": "ADkeyABCD9999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "adzuna"
    assert body["key_hint"].endswith("9999")


def test_adzuna_requires_both_parts(client):
    """Missing app_key → 400."""
    resp = client.put("/keys", json={"provider": "adzuna", "app_id": "12345678"})
    assert resp.status_code == 400


def test_empty_llm_key_rejected(client):
    resp = client.put("/keys", json={"provider": "openai", "api_key": "   "})
    assert resp.status_code == 400


def test_list_keys_shows_adzuna_app_key_hint(client):
    client.put("/keys", json={"provider": "adzuna", "app_id": "111", "app_key": "secretKEY7777"})
    resp = client.get("/keys")
    assert resp.status_code == 200
    row = next(k for k in resp.json() if k["provider"] == "adzuna")
    assert row["key_hint"].endswith("7777")


def test_get_llm_provider_ignores_adzuna(client, db, test_user):
    """An Adzuna key alone must NOT satisfy the LLM-key requirement."""
    from app.llm import get_llm_provider

    client.put("/keys", json={"provider": "adzuna", "app_id": "1", "app_key": "k123"})
    with pytest.raises(HTTPException):
        get_llm_provider(test_user.id, db)


def test_llm_provider_selected_alongside_adzuna(client, db, test_user):
    """With both an Adzuna and an Anthropic key, the LLM resolver picks Anthropic."""
    from app.llm import get_llm_provider

    client.put("/keys", json={"provider": "adzuna", "app_id": "1", "app_key": "k123"})
    client.put("/keys", json={"provider": "anthropic", "api_key": "sk-ant-abcd"})
    api_key, _model = get_llm_provider(test_user.id, db)
    assert api_key == "sk-ant-abcd"
