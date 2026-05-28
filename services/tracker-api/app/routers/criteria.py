"""
Criteria router — per-user job search criteria.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/criteria", tags=["criteria"])


def _get_or_404(criteria_id: UUID, user: models.User, db: Session) -> models.Criteria:
    obj = (
        db.query(models.Criteria)
        .filter(models.Criteria.id == criteria_id, models.Criteria.user_id == user.id)
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Criteria not found")
    return obj


@router.get("", response_model=schemas.CriteriaOut)
def get_criteria_for_user(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return the user's active criteria, or 404 if none exists yet."""
    obj = (
        db.query(models.Criteria)
        .filter(models.Criteria.user_id == current_user.id, models.Criteria.is_active == True)  # noqa: E712
        .order_by(models.Criteria.updated_at.desc())
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="No criteria found")
    return obj


@router.put("", response_model=schemas.CriteriaOut)
def upsert_criteria(
    payload: schemas.CriteriaCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create criteria if none exists, otherwise update the active one."""
    obj = (
        db.query(models.Criteria)
        .filter(models.Criteria.user_id == current_user.id, models.Criteria.is_active == True)  # noqa: E712
        .order_by(models.Criteria.updated_at.desc())
        .first()
    )
    if obj:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(obj, field, value)
    else:
        obj = models.Criteria(**payload.model_dump(), user_id=current_user.id, is_active=True)
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/active", response_model=schemas.CriteriaOut)
def get_active_criteria(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = (
        db.query(models.Criteria)
        .filter(models.Criteria.user_id == current_user.id, models.Criteria.is_active == True)  # noqa: E712
        .order_by(models.Criteria.updated_at.desc())
        .first()
    )
    if not obj:
        raise HTTPException(status_code=404, detail="No active criteria found")
    return obj


@router.get("/{criteria_id}", response_model=schemas.CriteriaOut)
def get_criteria(
    criteria_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _get_or_404(criteria_id, current_user, db)


@router.patch("/{criteria_id}", response_model=schemas.CriteriaOut)
def update_criteria(
    criteria_id: UUID,
    payload: schemas.CriteriaUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = _get_or_404(criteria_id, current_user, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/{criteria_id}/activate", response_model=schemas.CriteriaOut)
def activate_criteria(
    criteria_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db.query(models.Criteria).filter(
        models.Criteria.user_id == current_user.id,
        models.Criteria.id != criteria_id,
    ).update({"is_active": False})
    obj = _get_or_404(criteria_id, current_user, db)
    obj.is_active = True
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{criteria_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_criteria(
    criteria_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = _get_or_404(criteria_id, current_user, db)
    db.delete(obj)
    db.commit()


# ── Scraper-facing (no auth) — returns union of all active users' criteria ──

@router.get("/scraper/union", include_in_schema=False)
def scraper_union_criteria(db: Session = Depends(get_db)):
    """
    Used by the scraper to get a combined keyword list from all active users.
    Returns merged job_titles and locations deduplicated.
    """
    all_criteria = (
        db.query(models.Criteria)
        .join(models.User)
        .filter(models.Criteria.is_active == True, models.User.is_approved == True)  # noqa: E712
        .all()
    )
    titles: set[str] = set()
    locations: set[str] = set()
    for c in all_criteria:
        for t in (c.job_titles or []):
            titles.add(t)
        # search_locations is the new field; fall back to legacy locations column
        for loc in (c.search_locations or c.locations or []):
            locations.add(loc)
    return {"job_titles": list(titles), "search_locations": list(locations)}
