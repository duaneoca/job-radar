"""
Profile router — per-user candidate profile (feeds the AI reviewer).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, resume_tailor, schemas
from app.database import get_db
from app.deps import get_current_user
from app.llm import get_llm_provider

router = APIRouter(prefix="/profile", tags=["profile"])


def _active_profile(user: models.User, db: Session) -> models.Profile | None:
    return (
        db.query(models.Profile)
        .filter(models.Profile.user_id == user.id, models.Profile.is_active == True)  # noqa: E712
        .order_by(models.Profile.updated_at.desc())
        .first()
    )


def _mark_resume_stale_if_changed(obj: models.Profile, updates: dict) -> None:
    """When resume_text changes, the structured parse is out of date — flag it so
    the next tailor re-ingests (lazy refresh)."""
    if "resume_text" in updates and (updates["resume_text"] or "") != (obj.resume_text or ""):
        obj.resume_structured_stale = True


def _get_or_404(profile_id: UUID, user: models.User, db: Session) -> models.Profile:
    obj = (
        db.query(models.Profile)
        .filter(models.Profile.id == profile_id, models.Profile.user_id == user.id)
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Profile not found")
    return obj


@router.get("", response_model=schemas.ProfileOut)
def get_profile_for_user(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return the user's active profile, or 404 if none exists yet."""
    obj = (
        db.query(models.Profile)
        .filter(models.Profile.user_id == current_user.id, models.Profile.is_active == True)  # noqa: E712
        .order_by(models.Profile.updated_at.desc())
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="No profile found")
    return obj


@router.put("", response_model=schemas.ProfileOut)
def upsert_profile(
    payload: schemas.ProfileCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create profile if none exists, otherwise update the active one."""
    obj = (
        db.query(models.Profile)
        .filter(models.Profile.user_id == current_user.id, models.Profile.is_active == True)  # noqa: E712
        .order_by(models.Profile.updated_at.desc())
        .first()
    )
    if obj:
        updates = payload.model_dump(exclude_unset=True)
        _mark_resume_stale_if_changed(obj, updates)
        for field, value in updates.items():
            setattr(obj, field, value)
    else:
        obj = models.Profile(**payload.model_dump(), user_id=current_user.id, is_active=True)
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/resume/ingest", response_model=schemas.ResumeIngestOut)
def ingest_resume(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Parse the active profile's résumé text into structured JSON (stored) and
    return it plus the derived honesty facts. Clears the stale flag. Idempotent —
    safe to call repeatedly; uses the user's own LLM key (BYOK)."""
    obj = _active_profile(current_user, db)
    if not obj:
        raise HTTPException(status_code=404, detail="No active profile found")

    api_key, model = get_llm_provider(current_user.id, db)
    structured = resume_tailor.parse_resume_text(obj.resume_text, api_key, model)

    obj.resume_structured = structured.model_dump()
    obj.resume_structured_stale = False
    db.commit()

    return schemas.ResumeIngestOut(
        structured=structured,
        honesty_facts=resume_tailor.derive_honesty_facts(structured),
        stale=False,
    )


@router.put("/resume-template-settings")
def set_default_template_settings(
    payload: schemas.PrintSettingsIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Set the user's *default* print knobs (template/font/density/margin/accent) on the
    active profile. Per-job copies override this. Sanitized server-side."""
    obj = _active_profile(current_user, db)
    if not obj:
        raise HTTPException(status_code=404, detail="No active profile found")
    obj.resume_template_settings = payload.settings.model_dump()
    db.commit()
    return obj.resume_template_settings


@router.get("/active", response_model=schemas.ProfileOut)
def get_active_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = (
        db.query(models.Profile)
        .filter(models.Profile.user_id == current_user.id, models.Profile.is_active == True)  # noqa: E712
        .order_by(models.Profile.updated_at.desc())
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="No active profile found")
    return obj


@router.get("/{profile_id}", response_model=schemas.ProfileOut)
def get_profile(
    profile_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _get_or_404(profile_id, current_user, db)


@router.patch("/{profile_id}", response_model=schemas.ProfileOut)
def update_profile(
    profile_id: UUID,
    payload: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = _get_or_404(profile_id, current_user, db)
    updates = payload.model_dump(exclude_unset=True)
    _mark_resume_stale_if_changed(obj, updates)
    for field, value in updates.items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{profile_id}/activate", response_model=schemas.ProfileOut)
def activate_profile(
    profile_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db.query(models.Profile).filter(
        models.Profile.user_id == current_user.id,
        models.Profile.id != profile_id,
    ).update({"is_active": False})
    obj = _get_or_404(profile_id, current_user, db)
    obj.is_active = True
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = _get_or_404(profile_id, current_user, db)
    db.delete(obj)
    db.commit()
