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

# There is deliberately NO default model. Picking one on the user's behalf spends
# their money on a model they didn't choose — the gap between the cheapest and the
# most capable model is two orders of magnitude — and any model we hardcode has a
# shelf life (a retired Gemini default once made every review silently score
# nothing). A key with no model is an error the user is shown, not a gap we fill.

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
    # Google (keyed without prefix for matching after stripping "gemini/").
    # 1.5 and 2.0 are retired; these are descriptors only, and the Settings
    # dropdown lists whatever Google actually returns for the user's key.
    "gemini-2.5-flash-lite": "Fastest · lowest cost",
    "gemini-2.5-flash":      "Fast · low cost",
    "gemini-2.5-pro":        "Most capable · higher cost",
    "gemini-3.5-flash-lite": "Fastest · latest generation",
    "gemini-3.5-flash":      "Balanced · latest generation",
    "gemini-3.6-flash":      "Newest · balanced",
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

NO_MODEL_DETAIL = (
    "No model selected for your AI provider. "
    "Go to Settings → API Keys and choose a model."
)

MODEL_GONE_DETAIL = (
    "The selected model is no longer available. "
    "Go to Settings → API Keys and choose a different model."
)


def model_for_key(key: models.UserAPIKey) -> str | None:
    """LiteLLM model string for a key, or None when the user hasn't chosen one.

    Returns None rather than "" so callers can't accidentally pass a falsy-but-
    present model to litellm.
    """
    return key.preferred_model or None


def get_active_llm_key(user_id: UUID, db: Session):
    """The user's *active* LLM key — single source of truth for which key every
    consumer (scoring, research, email agent) uses.

    1. The explicitly-selected provider (`users.selected_llm_provider`), if a key
       exists — even if that key has no model. Sliding to a different provider
       because the chosen one is misconfigured would spend money on an account
       the user didn't pick; better to surface the error against their choice.
    2. Otherwise best available by priority order (Anthropic → OpenAI → Google →
       Groq), preferring a key that actually has a model. If none do, the first
       key found is returned so the error can name a real provider.

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

    fallback = None
    for provider in models.LLM_PROVIDERS:  # priority order
        key_obj = (
            db.query(models.UserAPIKey)
            .filter(
                models.UserAPIKey.user_id == user_id,
                models.UserAPIKey.provider == provider,
            )
            .first()
        )
        if key_obj is None:
            continue
        if key_obj.preferred_model:
            return key_obj
        if fallback is None:
            fallback = key_obj
    return fallback


def get_llm_provider(user_id: UUID, db: Session) -> tuple[str, str]:
    """
    Return (api_key, litellm_model) for the user's active LLM key.
    Raises 400 if no key is configured, or if the active key has no model.
    """
    key_obj = get_active_llm_key(user_id, db)
    if key_obj is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No AI API key configured. "
                "Add an Anthropic, OpenAI, Google, or Groq key in Settings → API Keys."
            ),
        )

    model = model_for_key(key_obj)
    if not model:
        raise HTTPException(status_code=400, detail=NO_MODEL_DETAIL)

    return decrypt_api_key(key_obj.encrypted_key), model


# ── Recording permanent key failures ──────────────────────────────────────────

def record_key_error(db: Session, key: models.UserAPIKey, kind: str, detail: str) -> None:
    """Remember a PERMANENT provider rejection so the UI can tell the user.

    Callers must have already decided the failure is permanent — a rate limit or a
    5xx must never reach here.
    """
    key.last_error_kind = kind
    key.last_error = (detail or "")[:1000]
    key.last_error_at = models.utcnow()
    db.commit()


def clear_key_error(db: Session, key: models.UserAPIKey) -> None:
    """Clear a recorded failure after a successful call. No-op when nothing is set,
    so the happy path doesn't write on every completion."""
    if key.last_error_kind is None and key.last_error is None and key.last_error_at is None:
        return
    key.last_error_kind = None
    key.last_error = None
    key.last_error_at = None
    db.commit()


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


def _looks_like_dead_model(exc: Exception) -> bool:
    """Whether an error message names the model as the thing that was rejected.

    Only ever consulted for errors already known NOT to be transient — a 429 body
    containing the word "invalid" must not be read as a retired model.
    """
    err = str(exc).lower()
    return "model" in err and any(
        w in err for w in ("not found", "deprecated", "invalid", "does not exist")
    )


# Mirrors ai-reviewer's transient_kind(). The two services share no package, and
# this copy exists so a foreground failure reaches the same banner as a
# background one — see llm_errors.py there for why the split matters.
_RATE_LIMIT_PHRASES = ("rate limit", "ratelimit", "quota", "resource_exhausted", "too many requests")

# The provider did not answer. Distinct from an unexpected exception in OUR code,
# which stays an operator problem and is deliberately not recorded below.
_PROVIDER_DOWN = (
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
)


def _transient_kind(exc: Exception) -> str:
    """Throttled, or simply not answering? Type first, then explicit wording."""
    if isinstance(exc, litellm.RateLimitError):
        return models.KEY_ERROR_RATE_LIMITED
    if any(p in str(exc).lower() for p in _RATE_LIMIT_PHRASES):
        return models.KEY_ERROR_RATE_LIMITED
    return models.KEY_ERROR_PROVIDER_UNAVAILABLE


def _remember(db: Session | None, user_id: UUID | None, kind: str, detail: str) -> None:
    """Best-effort record of a permanent failure against the user's active key.

    Never raises: the caller is already returning an error to the user, and losing
    the breadcrumb is better than replacing their error with ours.
    """
    if db is None or user_id is None:
        return
    try:
        key_obj = get_active_llm_key(user_id, db)
        if key_obj is not None:
            record_key_error(db, key_obj, kind, detail)
    except Exception:
        logger.warning("Could not record key error for user %s", user_id, exc_info=True)


def llm_complete(
    system: str,
    messages: list[dict],
    api_key: str,
    model: str,
    max_tokens: int = 1024,
    db: Session | None = None,
    user_id: UUID | None = None,
) -> str:
    """
    Call the LLM and return the response text.
    Raises HTTPException on auth failure, rate limit, or other API errors.

    Pass `db` and `user_id` to also persist the verdict on the user's active key
    (permanent failures recorded, success clears) so the UI can surface it. Both
    are optional — some call sites have no session in scope.
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
    except litellm.AuthenticationError as e:
        _remember(db, user_id, models.KEY_ERROR_INVALID_KEY, str(e))
        raise HTTPException(status_code=400, detail="Invalid API key. Check Settings → API Keys.")
    except litellm.RateLimitError as e:
        # litellm already retried this internally (num_retries above), so by the
        # time it surfaces the throttle has outlasted its retries — the same bar
        # the background worker uses before recording. The user gets an immediate
        # error either way; the record is what explains why *background* scoring
        # is also stalled.
        _remember(db, user_id, models.KEY_ERROR_RATE_LIMITED, str(e))
        logger.warning("Rate limited by provider (model=%s)", model)
        raise HTTPException(status_code=429, detail="AI provider rate limit reached. Try again later.")
    except _PROVIDER_DOWN as e:
        # WARNING, not ERROR: nothing here is the operator's to fix, and these
        # used to fall through to the catch-all below and log at ERROR — which
        # would put a user's provider outage in the hourly digest.
        _remember(db, user_id, _transient_kind(e), str(e))
        logger.warning("AI provider unreachable (model=%s): %s", model, e)
        raise HTTPException(status_code=502, detail="The AI provider isn't responding. Try again shortly.")
    except litellm.BadRequestError as e:
        if _looks_like_dead_model(e):
            _remember(db, user_id, models.KEY_ERROR_INVALID_MODEL, str(e))
            raise HTTPException(status_code=400, detail=MODEL_GONE_DETAIL)
        raise HTTPException(status_code=400, detail=f"Bad request to AI provider: {e}")
    except Exception as e:
        if _looks_like_dead_model(e):
            _remember(db, user_id, models.KEY_ERROR_INVALID_MODEL, str(e))
            raise HTTPException(status_code=400, detail=MODEL_GONE_DETAIL)
        logger.exception("LLM completion failed (model=%s)", model)
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    if db is not None and user_id is not None:
        try:
            key_obj = get_active_llm_key(user_id, db)
            if key_obj is not None:
                clear_key_error(db, key_obj)
        except Exception:
            logger.warning("Could not clear key error for user %s", user_id, exc_info=True)

    return response.choices[0].message.content
