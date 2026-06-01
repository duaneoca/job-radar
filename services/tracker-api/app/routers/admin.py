"""
Admin router — user management, pending approvals, manual triggers.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_admin
from app.email import notify_account_approved
from app.security import hash_password

# Celery producer — sends tasks to scraper and ai-reviewer queues
_celery = Celery(broker=settings.redis_url)

router = APIRouter(prefix="/admin", tags=["admin"])

logger = logging.getLogger(__name__)

# Statuses that are candidates for cleanup after terminal_ttl_days
_TERMINAL_STATUSES = [
    models.JobStatus.DISMISSED,
    models.JobStatus.REJECTED,
    models.JobStatus.EXPIRED,
]


def _do_cleanup(db: Session) -> dict:
    """
    1. Hard-delete UserJobReview rows whose status is terminal (dismissed /
       rejected / expired) and whose updated_at is older than terminal_ttl_days.
       The DB-level CASCADE on timeline_events.review_id removes timeline rows
       automatically.
    2. Hard-delete Job rows that now have no reviews from any user (true orphans).

    Returns a dict with deletion counts.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.terminal_ttl_days)

    reviews_deleted = (
        db.query(models.UserJobReview)
        .filter(
            models.UserJobReview.status.in_(_TERMINAL_STATUSES),
            models.UserJobReview.updated_at < cutoff,
        )
        .delete(synchronize_session=False)
    )

    # Any job whose last review was just deleted is now an orphan — remove it.
    # The subquery runs after the review deletions (same transaction, Postgres
    # READ COMMITTED sees our own changes), so only truly orphaned jobs match.
    reviewed_job_ids = db.query(models.UserJobReview.job_id)
    jobs_deleted = (
        db.query(models.Job)
        .filter(models.Job.id.not_in(reviewed_job_ids))
        .delete(synchronize_session=False)
    )

    db.commit()
    logger.info(
        "cleanup_jobs: %d terminal reviews deleted, %d orphan jobs deleted",
        reviews_deleted, jobs_deleted,
    )
    return {"reviews_deleted": reviews_deleted, "orphan_jobs_deleted": jobs_deleted}


@router.get("/users", response_model=schemas.PaginatedUsers)
def list_users(
    approved: Optional[bool] = Query(None, description="Filter by approval status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """List all users, optionally filtered by approval status."""
    q = db.query(models.User)
    if approved is not None:
        q = q.filter(models.User.is_approved == approved)
    total = q.count()
    items = q.order_by(models.User.created_at.desc()).offset(skip).limit(limit).all()
    return schemas.PaginatedUsers(total=total, items=items)


@router.post("/users/{user_id}/approve", response_model=schemas.AdminUserOut)
def approve_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_approved = True
    db.commit()
    db.refresh(user)
    notify_account_approved(user.email, user.full_name)
    return user


@router.post("/users/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Permanently delete a pending (unapproved) user."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_approved:
        raise HTTPException(status_code=400, detail="Cannot reject an already-approved user")
    db.delete(user)
    db.commit()


@router.patch("/users/{user_id}/toggle-admin", response_model=schemas.AdminUserOut)
def toggle_admin(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_admin.id:
        raise HTTPException(status_code=400, detail="Cannot change your own admin status")
    user.is_admin = not user.is_admin
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: UUID,
    payload: schemas.AdminResetPasswordRequest,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Admin sets a temporary password; user is forced to change it on next login."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = True
    db.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
):
    """Permanently delete any user and all their data."""
    if str(user_id) == str(current_admin.id):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()


# ── Manual triggers ───────────────────────────────────────────────────────────

@router.post("/trigger-scrape", status_code=status.HTTP_202_ACCEPTED)
def trigger_scrape(
    _: models.User = Depends(get_current_admin),
):
    """Enqueue an immediate scrape run (normally runs every 2 hours via Celery Beat)."""
    _celery.send_task("app.tasks.scrape_all")
    return {"detail": "Scrape enqueued"}


@router.post("/cleanup-jobs")
def cleanup_jobs_endpoint(
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """
    Hard-delete terminal-status reviews (dismissed/rejected/expired) older than
    terminal_ttl_days, then remove any jobs that become orphaned.
    Also available as POST /admin/internal/cleanup for the scheduled task.
    """
    return _do_cleanup(db)


@router.post("/internal/cleanup", include_in_schema=False)
def cleanup_jobs_internal(db: Session = Depends(get_db)):
    """Called by the scraper's daily Celery Beat task — no user auth required."""
    return _do_cleanup(db)


@router.post("/trigger-evaluate", status_code=status.HTTP_202_ACCEPTED)
def trigger_evaluate(
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Enqueue AI review for every job that has no score yet for any approved user."""
    unreviewed = (
        db.query(models.UserJobReview)
        .filter(models.UserJobReview.ai_score == None)  # noqa: E711
        .all()
    )
    count = 0
    for review in unreviewed:
        _celery.send_task(
            "app.tasks.review_job",
            args=[str(review.job_id)],
            queue="review",
        )
        count += 1
    return {"detail": f"{count} jobs enqueued for evaluation"}
