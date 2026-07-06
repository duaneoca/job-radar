"""
API keys router — store encrypted provider keys per user.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user, require_internal_token
from app.security import decrypt_api_key, encrypt_api_key

router = APIRouter(prefix="/keys", tags=["api-keys"])


def _pack_secret(payload: schemas.APIKeyUpsert) -> str:
    """Return the plaintext secret to encrypt for this provider.

    Adzuna uses a two-part credential packed as JSON; everyone else uses the
    single api_key string. Raises 400 if required fields are missing.
    """
    if payload.provider == models.LLMProvider.ADZUNA:
        app_id = (payload.app_id or "").strip()
        app_key = (payload.app_key or "").strip()
        if not (app_id and app_key):
            raise HTTPException(status_code=400, detail="Adzuna requires both app_id and app_key")
        return json.dumps({"app_id": app_id, "app_key": app_key})
    secret = (payload.api_key or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    return secret


def _hint_for(provider: models.LLMProvider, plaintext: str) -> str:
    """Display hint — last 4 chars of the secret (the app_key for Adzuna)."""
    src = plaintext
    if provider == models.LLMProvider.ADZUNA:
        try:
            src = json.loads(plaintext).get("app_key", "")
        except Exception:
            src = ""
    return f"…{src[-4:]}" if len(src) >= 4 else "…"


@router.get("", response_model=list[schemas.APIKeyOut])
def list_keys(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from app.llm import get_active_llm_key
    keys = (
        db.query(models.UserAPIKey)
        .filter(models.UserAPIKey.user_id == current_user.id)
        .all()
    )
    # Mark the effective active LLM key (explicit selection, else priority) so the
    # UI radio always reflects what scoring/research/the agent actually use.
    active = get_active_llm_key(current_user.id, db)
    active_provider = active.provider if active else None

    result = []
    for k in keys:
        try:
            plain = decrypt_api_key(k.encrypted_key)
            hint = _hint_for(k.provider, plain)
        except Exception:
            hint = "…?????"
        result.append(schemas.APIKeyOut(
            provider=k.provider,
            key_hint=hint,
            preferred_model=k.preferred_model,
            updated_at=k.updated_at,
            active=(k.provider == active_provider),
        ))
    return result


@router.put("/active", response_model=list[schemas.APIKeyOut])
def set_active_llm(
    payload: schemas.ActiveKeyUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Set the active LLM key (radio button). `provider=null` clears the selection
    and reverts to priority order. Registered before /{provider} (route-ordering)."""
    if payload.provider is not None:
        if payload.provider not in models.LLM_PROVIDERS:
            raise HTTPException(status_code=400, detail="Not an LLM provider")
        has_key = (
            db.query(models.UserAPIKey)
            .filter(
                models.UserAPIKey.user_id == current_user.id,
                models.UserAPIKey.provider == payload.provider,
            )
            .first()
        )
        if not has_key:
            raise HTTPException(status_code=404, detail="No key configured for that provider")
    current_user.selected_llm_provider = payload.provider
    db.commit()
    return list_keys(db=db, current_user=current_user)


@router.put("", response_model=schemas.APIKeyOut)
def upsert_key(
    payload: schemas.APIKeyUpsert,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add or replace a provider API key. The plaintext is encrypted immediately."""
    secret = _pack_secret(payload)
    encrypted = encrypt_api_key(secret)
    existing = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == current_user.id,
            models.UserAPIKey.provider == payload.provider,
        )
        .first()
    )
    if existing:
        existing.encrypted_key = encrypted
        existing.preferred_model = payload.preferred_model or None
        db.commit()
        db.refresh(existing)
        key_obj = existing
    else:
        key_obj = models.UserAPIKey(
            user_id=current_user.id,
            provider=payload.provider,
            encrypted_key=encrypted,
            preferred_model=payload.preferred_model or None,
        )
        db.add(key_obj)
        db.commit()
        db.refresh(key_obj)

    return schemas.APIKeyOut(
        provider=key_obj.provider,
        key_hint=_hint_for(key_obj.provider, secret),
        preferred_model=key_obj.preferred_model,
        updated_at=key_obj.updated_at,
    )


@router.get("/{provider}/models")
def list_models_for_provider(
    provider: models.LLMProvider,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Fetch the live model list from the provider's API using the user's stored key.
    Returns [{id, label, descriptor}] filtered to chat/generation models only.
    """
    from app.llm import fetch_provider_models
    key_obj = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == current_user.id,
            models.UserAPIKey.provider == provider,
        )
        .first()
    )
    if not key_obj:
        raise HTTPException(status_code=404, detail="No key configured for this provider")
    api_key = decrypt_api_key(key_obj.encrypted_key)
    return fetch_provider_models(provider.value, api_key)


@router.patch("/{provider}", response_model=schemas.APIKeyOut)
def update_key_model(
    provider: models.LLMProvider,
    payload: schemas.APIKeyModelUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update just the preferred model for an existing key — no key re-entry needed."""
    existing = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == current_user.id,
            models.UserAPIKey.provider == provider,
        )
        .first()
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Key not found")
    existing.preferred_model = payload.preferred_model or None
    db.commit()
    db.refresh(existing)
    try:
        plain = decrypt_api_key(existing.encrypted_key)
        hint = f"…{plain[-4:]}" if len(plain) >= 4 else "…"
    except Exception:
        hint = "…?????"
    return schemas.APIKeyOut(
        provider=existing.provider,
        key_hint=hint,
        preferred_model=existing.preferred_model,
        updated_at=existing.updated_at,
    )


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(
    provider: models.LLMProvider,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    existing = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == current_user.id,
            models.UserAPIKey.provider == provider,
        )
        .first()
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Key not found")
    db.delete(existing)
    db.commit()


# ── Internal — used by ai-reviewer service ────────────────────
# IMPORTANT: /internal/{user_id}/llm must be defined BEFORE /internal/{user_id}/{provider}
# so FastAPI matches the literal "llm" segment before the dynamic enum parameter.

@router.get("/internal/{user_id}/llm", include_in_schema=False)
def get_best_llm_key(
    user_id: str,
    db: Session = Depends(get_db),
    _it: None = Depends(require_internal_token),
):
    """
    Returns the decrypted API key and LiteLLM model string for the user's *active*
    LLM key — the explicit selection, else priority order. Called by ai-reviewer.
    """
    from app.llm import get_active_llm_key, model_for_key
    key_obj = get_active_llm_key(user_id, db)
    if not key_obj:
        raise HTTPException(status_code=404, detail="No AI key configured")
    return {
        "api_key": decrypt_api_key(key_obj.encrypted_key),
        "model": model_for_key(key_obj),
        "provider": key_obj.provider.value,
    }


@router.get("/internal/{user_id}/{provider}", include_in_schema=False)
def get_key_for_service(
    user_id: str,
    provider: models.LLMProvider,
    db: Session = Depends(get_db),
    _it: None = Depends(require_internal_token),
):
    """
    Returns the decrypted API key for a given user+provider.
    Called by the ai-reviewer service — not exposed in public API docs.
    """
    key_obj = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == user_id,
            models.UserAPIKey.provider == provider,
        )
        .first()
    )
    if not key_obj:
        raise HTTPException(status_code=404, detail="No key configured")
    return {"api_key": decrypt_api_key(key_obj.encrypted_key)}
