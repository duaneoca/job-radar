"""
API keys router — store encrypted provider keys per user.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user
from app.security import decrypt_api_key, encrypt_api_key

router = APIRouter(prefix="/keys", tags=["api-keys"])


@router.get("", response_model=list[schemas.APIKeyOut])
def list_keys(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    keys = (
        db.query(models.UserAPIKey)
        .filter(models.UserAPIKey.user_id == current_user.id)
        .all()
    )
    result = []
    for k in keys:
        try:
            plain = decrypt_api_key(k.encrypted_key)
            hint = f"…{plain[-4:]}" if len(plain) >= 4 else "…"
        except Exception:
            hint = "…?????"
        result.append(schemas.APIKeyOut(
            provider=k.provider,
            key_hint=hint,
            preferred_model=k.preferred_model,
            updated_at=k.updated_at,
        ))
    return result


@router.put("", response_model=schemas.APIKeyOut)
def upsert_key(
    payload: schemas.APIKeyUpsert,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add or replace a provider API key. The plaintext is encrypted immediately."""
    if not payload.api_key.strip():
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    encrypted = encrypt_api_key(payload.api_key.strip())
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

    plain = payload.api_key.strip()
    hint = f"…{plain[-4:]}" if len(plain) >= 4 else "…"
    return schemas.APIKeyOut(
        provider=key_obj.provider,
        key_hint=hint,
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
def get_best_llm_key(user_id: str, db: Session = Depends(get_db)):
    """
    Returns the decrypted API key and LiteLLM model string for the user's
    best available provider (Anthropic → OpenAI → Google → Groq).
    Called by the ai-reviewer service.
    """
    from app.llm import PROVIDER_MODELS
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
            return {
                "api_key": decrypt_api_key(key_obj.encrypted_key),
                "model": key_obj.preferred_model or model,
                "provider": provider.value,
            }
    raise HTTPException(status_code=404, detail="No AI key configured")


@router.get("/internal/{user_id}/{provider}", include_in_schema=False)
def get_key_for_service(
    user_id: str,
    provider: models.LLMProvider,
    db: Session = Depends(get_db),
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
