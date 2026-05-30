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

import litellm
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.security import decrypt_api_key

logger = logging.getLogger(__name__)

# Silence LiteLLM's verbose logging
litellm.suppress_debug_info = True

# Best model per provider — cost/speed balance comparable to claude-haiku
PROVIDER_MODELS: dict[models.LLMProvider, str] = {
    models.LLMProvider.ANTHROPIC: "claude-haiku-4-5",
    models.LLMProvider.OPENAI:    "gpt-4o-mini",
    models.LLMProvider.GOOGLE:    "gemini/gemini-1.5-flash",
    models.LLMProvider.GROQ:      "groq/llama-3.3-70b-versatile",
}


def get_llm_provider(user_id: UUID, db: Session) -> tuple[str, str]:
    """
    Return (api_key, litellm_model) for the user's best available provider.
    Tries providers in priority order; raises 400 if none are configured.
    """
    for provider, model in PROVIDER_MODELS.items():
        key_obj = (
            db.query(models.UserAPIKey)
            .filter(
                models.UserAPIKey.user_id == user_id,
                models.UserAPIKey.provider == provider,
            )
            .first()
        )
        if key_obj:
            return decrypt_api_key(key_obj.encrypted_key), key_obj.preferred_model or model

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
        )
        return response.choices[0].message.content
    except litellm.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid API key. Check Settings → API Keys.")
    except litellm.RateLimitError:
        raise HTTPException(status_code=429, detail="AI provider rate limit reached. Try again later.")
    except litellm.BadRequestError as e:
        raise HTTPException(status_code=400, detail=f"Bad request to AI provider: {e}")
    except Exception as e:
        logger.exception("LLM completion failed (model=%s)", model)
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")
