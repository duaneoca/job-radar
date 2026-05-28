"""
Profile router — per-user candidate profile (feeds the AI reviewer).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/profile", tags=["profile"])


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
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(obj, field, value)
    else:
        obj = models.Profile(**payload.model_dump(), user_id=current_user.id, is_active=True)
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


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
    for field, value in payload.model_dump(exclude_unset=True).items():
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
