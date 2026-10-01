"""xAI (the Grok models) as a fifth LLM provider — distinct from Groq, which
hosts open models like Llama. The model list is the part most likely to break
silently: xAI documents no list endpoint, so the fetcher accepts both plausible
shapes and filters out what can't answer a chat completion."""

import httpx
import pytest

from app import models
from app.llm import fetch_provider_models
from app.routers.keys import _hint_for


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


MODELS = [
    {"id": "grok-4.3", "created": 100},
    {"id": "grok-4.7", "created": 300},
    {"id": "grok-imagine-image", "created": 400},
    {"id": "grok-imagine-video", "created": 401},
    {"id": "grok-4.20-multi-agent-0309", "created": 200},
    {"id": "grok-4.20-0309-reasoning", "created": 250},
]


@pytest.mark.parametrize("shape", ["data", "models"])
def test_lists_chat_models_newest_first_with_litellm_prefix(monkeypatch, shape):
    seen = {}

    def fake_get(url, headers=None, **kw):
        seen["url"], seen["auth"] = url, headers.get("Authorization")
        return _Resp({shape: MODELS})
    monkeypatch.setattr(httpx, "get", fake_get)

    got = fetch_provider_models("xai", "xai-test")
    assert seen == {"url": "https://api.x.ai/v1/models", "auth": "Bearer xai-test"}
    assert [m["id"] for m in got] == [
        "xai/grok-4.7", "xai/grok-4.20-0309-reasoning", "xai/grok-4.3",
    ]
    assert got[0]["descriptor"] == "Newest · balanced"


def test_xai_is_a_selectable_llm_and_ranks_before_groq():
    order = models.LLM_PROVIDERS
    assert models.LLMProvider.XAI in order
    assert order.index(models.LLMProvider.XAI) < order.index(models.LLMProvider.GROQ)


def test_key_round_trip(client, db):
    """Stored as VARCHAR(50), so a new provider needs no migration."""
    r = client.put("/keys", json={"provider": "xai", "api_key": "xai-abcdef123456",
                                  "preferred_model": "xai/grok-4.3"})
    assert r.status_code == 200, r.text
    row = client.get("/keys").json()[0]
    assert row["provider"] == "xai" and row["preferred_model"] == "xai/grok-4.3"
    assert row["key_hint"] == _hint_for(models.LLMProvider.XAI, "xai-abcdef123456")
