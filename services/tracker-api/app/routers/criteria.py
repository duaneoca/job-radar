"""
Criteria router — per-user job search criteria.
"""

import json
import logging
from uuid import UUID

import redis as _redis_lib
from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_internal_token
from app.security import decrypt_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/criteria", tags=["criteria"])

# Producer-only Celery + a Redis client for debouncing criteria-change scrapes.
_celery = Celery(broker=settings.redis_url)
_redis_client = _redis_lib.from_url(settings.redis_url, socket_connect_timeout=1)
_SCRAPE_DEBOUNCE_SECONDS = 120


def _maybe_enqueue_scrape(user_id: UUID) -> None:
    """Fire a debounced per-user scrape after a criteria change.

    Best-effort: never blocks or fails the request. A Redis SET NX EX gates it so
    rapid successive saves coalesce into a single scrape within the debounce
    window (avoids a burst of scrapes / Adzuna calls while the user edits).
    """
    try:
        if _redis_client.set(f"scrape_debounce:{user_id}", "1", nx=True, ex=_SCRAPE_DEBOUNCE_SECONDS):
            _celery.send_task("app.tasks.scrape_user", args=[str(user_id)])
    except Exception:
        logger.warning("Could not enqueue scrape for user %s", user_id, exc_info=True)


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
    _maybe_enqueue_scrape(current_user.id)
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
    _maybe_enqueue_scrape(current_user.id)
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
    _maybe_enqueue_scrape(current_user.id)
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


# ── Scraper-facing (no auth, in-cluster only) ────────────────

@router.get(
    "/scraper/user-configs",
    response_model=list[schemas.ScraperUserConfig],
    include_in_schema=False,
)
def scraper_user_configs(
    db: Session = Depends(get_db),
    _it: None = Depends(require_internal_token),
):
    """
    Internal — used by the per-user scraper (BYOK). Returns each approved user's
    active criteria plus their decrypted Adzuna credentials (or null if they
    haven't provided a key).

    Returns decrypted secrets, so this MUST stay in-cluster only — blocked from
    external access by NetworkPolicy (same posture as /agent/config; JR-5).
    """
    active = (
        db.query(models.Criteria)
        .join(models.User)
        .filter(models.Criteria.is_active == True, models.User.is_approved == True)  # noqa: E712
        .all()
    )

    # Preload all Adzuna keys in one query, indexed by user.
    adzuna_by_user = {
        row.user_id: row
        for row in db.query(models.UserAPIKey)
        .filter(models.UserAPIKey.provider == models.LLMProvider.ADZUNA)
        .all()
    }

    configs: list[schemas.ScraperUserConfig] = []
    for c in active:
        adzuna = None
        key_row = adzuna_by_user.get(c.user_id)
        if key_row:
            try:
                blob = json.loads(decrypt_api_key(key_row.encrypted_key))
                if blob.get("app_id") and blob.get("app_key"):
                    adzuna = schemas.ScraperAdzunaCreds(
                        app_id=blob["app_id"], app_key=blob["app_key"]
                    )
            except Exception:
                logger.warning("Could not decode Adzuna creds for user %s", c.user_id)

        configs.append(
            schemas.ScraperUserConfig(
                user_id=c.user_id,
                job_titles=c.job_titles or [],
                search_locations=c.search_locations or c.locations or [],
                work_style=c.work_style or "any",
                target_companies=c.target_companies or [],
                adzuna=adzuna,
            )
        )
    return configs
