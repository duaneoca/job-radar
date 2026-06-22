"""
Thin LiteLLM wrapper for multi-provider AI generation.

Supported providers (tried in priority order when resolving a user's key):
  Anthropic → OpenAI → Google → Groq

Usage:
    api_key, model = get_llm_provider(user_id, db)
    text = llm_complete(system="...", messages=[...], api_key=api_key, model=model)
"""
import logging
from uuid import UUID

import httpx
import litellm
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.security import decrypt_api_key

logger = logging.getLogger(__name__)

# Silence LiteLLM's verbose logging
litellm.suppress_debug_info = True

# Default model per provider — cost/speed balance comparable to claude-haiku
PROVIDER_MODELS: dict[models.LLMProvider, str] = {
    models.LLMProvider.ANTHROPIC: "claude-haiku-4-5",
    models.LLMProvider.OPENAI:    "gpt-4o-mini",
    models.LLMProvider.GOOGLE:    "gemini/gemini-1.5-flash",
    models.LLMProvider.GROQ:      "groq/llama-3.3-70b-versatile",
}

# Descriptors for known models — shown alongside model name in the UI
MODEL_DESCRIPTORS: dict[str, str] = {
    # Anthropic
    "claude-haiku-4-5":  "Fast · lowest cost",
    "claude-sonnet-4-6": "Balanced · recommended",
    "claude-opus-4-6":   "Most capable · higher cost",
    "claude-opus-4-7":   "Latest Opus · highest cost",
    # OpenAI
    "gpt-4o-mini":  "Fast · lowest cost",
    "gpt-4o":       "Balanced · recommended",
    "o1-mini":      "Reasoning · higher cost",
    "o1":           "Advanced reasoning · highest cost",
    "o3-mini":      "Fast reasoning",
    "o3":           "Advanced reasoning · highest cost",
    # Google (keyed without prefix for matching after stripping "gemini/")
    "gemini-1.5-flash":    "Fast · lowest cost",
    "gemini-1.5-pro":      "Balanced · recommended",
    "gemini-2.0-flash":    "Fast · latest generation",
    "gemini-2.0-pro":      "Most capable · higher cost",
    # Groq (keyed without prefix for matching after stripping "groq/")
    "llama-3.3-70b-versatile": "Balanced · free tier",
    "llama-3.1-8b-instant":    "Fastest · free tier",
    "mixtral-8x7b-32768":      "Long context · free tier",
    "llama3-70b-8192":         "Balanced · free tier",
}

# Prefixes that identify chat/completion models for OpenAI
_OPENAI_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def _descriptor(model_id: str) -> str | None:
    """Return a human descriptor for a model ID, stripping provider prefixes."""
    bare = model_id.removeprefix("gemini/").removeprefix("groq/")
    return MODEL_DESCRIPTORS.get(bare) or MODEL_DESCRIPTORS.get(model_id)


# ── Provider model listing ────────────────────────────────────────────────────

def fetch_provider_models(provider: str, api_key: str) -> list[dict]:
    """
    Query the provider's live models API and return a filtered list of
    chat/generation models as [{id, label, descriptor}].
    """
    try:
        if provider == "anthropic":
            return _fetch_anthropic_models(api_key)
        elif provider == "openai":
            return _fetch_openai_models(api_key)
        elif provider == "google":
            return _fetch_google_models(api_key)
        elif provider == "groq":
            return _fetch_groq_models(api_key)
    except httpx.HTTPStatusError as e:
        logger.warning("Provider model fetch failed (%s): %s", provider, e)
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch models from {provider}: {e.response.status_code}"
        )
    except Exception as e:
        logger.warning("Provider model fetch error (%s): %s", provider, e)
        raise HTTPException(status_code=502, detail=f"Could not fetch models: {e}")
    return []


def _fetch_anthropic_models(api_key: str) -> list[dict]:
    resp = httpx.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return [
        {
            "id": m["id"],
            "label": m.get("display_name") or m["id"],
            "descriptor": _descriptor(m["id"]),
        }
        for m in data
        if m.get("id", "").startswith("claude-")
    ]


def _fetch_openai_models(api_key: str) -> list[dict]:
    resp = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    filtered = [
        m for m in data
        if any(m["id"].startswith(p) for p in _OPENAI_CHAT_PREFIXES)
    ]
    # Sort newest first by created timestamp
    filtered.sort(key=lambda m: m.get("created", 0), reverse=True)
    return [
        {
            "id": m["id"],
            "label": m["id"],   # OpenAI doesn't provide display names
            "descriptor": _descriptor(m["id"]),
        }
        for m in filtered
    ]


def _fetch_google_models(api_key: str) -> list[dict]:
    resp = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("models", [])
    results = []
    for m in data:
        bare_name = m["name"].split("/")[-1]   # "models/gemini-1.5-flash" → "gemini-1.5-flash"
        if not bare_name.startswith("gemini-"):
            continue
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        litellm_id = f"gemini/{bare_name}"
        results.append({
            "id": litellm_id,
            "label": m.get("displayName") or bare_name,
            "descriptor": _descriptor(litellm_id),
        })
    return results


def _fetch_groq_models(api_key: str) -> list[dict]:
    resp = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    filtered = [m for m in data if not m["id"].startswith("whisper")]
    filtered.sort(key=lambda m: m.get("created", 0), reverse=True)
    return [
        {
            "id": f"groq/{m['id']}",
            "label": m["id"],
            "descriptor": _descriptor(f"groq/{m['id']}"),
        }
        for m in filtered
    ]


# ── Core helpers ──────────────────────────────────────────────────────────────

def model_for_key(key: models.UserAPIKey) -> str:
    """LiteLLM model string for a key — the user's preferred_model, else provider default."""
    return key.preferred_model or PROVIDER_MODELS.get(key.provider, "")


def get_active_llm_key(user_id: UUID, db: Session):
    """The user's *active* LLM key — single source of truth for which key every
    consumer (scoring, research, email agent) uses.

    1. The explicitly-selected provider (`users.selected_llm_provider`), if a key exists.
    2. Otherwise best available by priority order (Anthropic → OpenAI → Google → Groq).
    Returns the UserAPIKey row, or None if the user has no LLM key.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is not None and user.selected_llm_provider is not None:
        chosen = (
            db.query(models.UserAPIKey)
            .filter(
                models.UserAPIKey.user_id == user_id,
                models.UserAPIKey.provider == user.selected_llm_provider,
            )
            .first()
        )
        if chosen:
            return chosen

    for provider in PROVIDER_MODELS:  # priority order
        key_obj = (
            db.query(models.UserAPIKey)
            .filter(
                models.UserAPIKey.user_id == user_id,
                models.UserAPIKey.provider == provider,
            )
            .first()
        )
        if key_obj:
            return key_obj
    return None


def get_llm_provider(user_id: UUID, db: Session) -> tuple[str, str]:
    """
    Return (api_key, litellm_model) for the user's active LLM key.
    Raises 400 if none are configured.
    """
    key_obj = get_active_llm_key(user_id, db)
    if key_obj:
        return decrypt_api_key(key_obj.encrypted_key), model_for_key(key_obj)

    raise HTTPException(
        status_code=400,
        detail=(
            "No AI API key configured. "
            "Add an Anthropic, OpenAI, Google, or Groq key in Settings → API Keys."
        ),
    )


def get_tavily_key(user_id: UUID, db: Session) -> str | None:
    """Return the user's Tavily API key, or None if not configured."""
    key_obj = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == user_id,
            models.UserAPIKey.provider == models.LLMProvider.TAVILY,
        )
        .first()
    )
    return decrypt_api_key(key_obj.encrypted_key) if key_obj else None


def llm_complete(
    system: str,
    messages: list[dict],
    api_key: str,
    model: str,
    max_tokens: int = 1024,
) -> str:
    """
    Call the LLM and return the response text.
    Raises HTTPException on auth failure, rate limit, or other API errors.
    """
    full_messages = [{"role": "system", "content": system}] + messages
    try:
        response = litellm.completion(
            model=model,
            messages=full_messages,
            api_key=api_key,
            max_tokens=max_tokens,
            # Auto-retry transient provider hiccups (429 rate-limit windows, 529
            # "overloaded", brief timeouts) with exponential backoff. Absorbs the
            # per-minute spikes that free tiers hit; a hard daily limit still
            # surfaces cleanly after the retries are exhausted.
            num_retries=2,
        )
        return response.choices[0].message.content
    except litellm.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid API key. Check Settings → API Keys.")
    except litellm.RateLimitError:
        raise HTTPException(status_code=429, detail="AI provider rate limit reached. Try again later.")
    except litellm.BadRequestError as e:
        err = str(e).lower()
        if "model" in err and any(w in err for w in ("not found", "deprecated", "invalid", "does not exist")):
            raise HTTPException(
                status_code=400,
                detail="The selected model is no longer available. Go to Settings → API Keys and choose a different model.",
            )
        raise HTTPException(status_code=400, detail=f"Bad request to AI provider: {e}")
    except Exception as e:
        err = str(e).lower()
        if "model" in err and any(w in err for w in ("not found", "deprecated", "invalid", "does not exist")):
            raise HTTPException(
                status_code=400,
                detail="The selected model is no longer available. Go to Settings → API Keys and choose a different model.",
            )
        logger.exception("LLM completion failed (model=%s)", model)
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")
