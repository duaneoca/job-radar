"""
Jobs router — shared job pool + per-user review management.

POST /jobs          Scraper writes raw jobs here (no auth, internal service)
GET  /jobs          Current user's job list (flattened with their review data)
GET  /jobs/:id      Single job+review for current user
PATCH /jobs/:id     User updates their status/notes/contact info
POST /jobs/:id/ai-review   ai-reviewer posts scores (internal, uses user_id query param)
POST /jobs/enqueue-review  Admin trigger to re-queue all new jobs for a user
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import exists, func
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Celery producer — tracker-api only *sends* tasks, never runs workers
_celery = Celery(broker=settings.redis_url)


# ── Helpers ──────────────────────────────────────────────────

def _get_review_or_404(review_id: UUID, user: models.User, db: Session) -> models.UserJobReview:
    review = (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job), joinedload(models.UserJobReview.timeline))
        .filter(
            models.UserJobReview.id == review_id,
            models.UserJobReview.user_id == user.id,
        )
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="Job not found")
    return review


def _add_timeline(db: Session, review_id: UUID, event_type: str, description: str):
    db.add(models.TimelineEvent(
        review_id=review_id, event_type=event_type, description=description
    ))


def apply_status_change(
    review: models.UserJobReview,
    new_status: models.JobStatus,
    note: str,
    db: Session,
) -> None:
    """Apply a status transition + timeline entry to an existing review.

    Reused by the agent /interactions endpoint so the logic stays in one place.
    Caller must commit after calling this.
    """
    if new_status == review.status:
        return
    old = review.status.value
    _add_timeline(db, review.id, "status_change", f"Status: {old} → {new_status.value} — {note}")
    if new_status == models.JobStatus.APPLIED and not review.date_applied:
        review.date_applied = datetime.now(timezone.utc)
    review.status = new_status


def _ensure_review_for_user(job_id: UUID, user_id: UUID, db: Session) -> bool:
    """Create a NEW review for one user (per-user scrape attribution) and enqueue
    its AI review. Idempotent — does nothing if the user already has a review for
    this job. Returns True if a review was created.

    A job is attributed only to the user whose criteria found it (BYOK per-user
    scraping); there is no fan-out to all users.
    """
    exists = (
        db.query(models.UserJobReview)
        .filter(
            models.UserJobReview.user_id == user_id,
            models.UserJobReview.job_id == job_id,
        )
        .first()
    )
    if exists:
        return False

    review = models.UserJobReview(user_id=user_id, job_id=job_id, status=models.JobStatus.NEW)
    db.add(review)
    db.flush()
    _add_timeline(db, review.id, "status_change", "Job added — awaiting AI review")
    db.commit()

    _celery.send_task(
        "app.tasks.review_job",
        args=[str(job_id), str(user_id)],
        queue="review",
    )
    return True


# ── Scraper-facing endpoint (no auth, in-cluster only) ───────

@router.post("", response_model=schemas.JobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: schemas.JobCreate,
    response: Response,
    user_id: UUID = Query(..., description="Per-user scrape attribution (BYOK)"),
    db: Session = Depends(get_db),
):
    """
    Called by the per-user scraper. Deduplicates the shared Job by
    (external_id, source), then attributes it to `user_id` only — creates that
    user's review + enqueues their AI review. No fan-out to other users.

    Returns 201 for a newly created Job, 200 if the Job already existed (the
    user still gets their own review either way).
    """
    existing = None
    if payload.external_id:
        existing = (
            db.query(models.Job)
            .filter(
                models.Job.external_id == payload.external_id,
                models.Job.source == payload.source,
            )
            .first()
        )

    if existing:
        response.status_code = status.HTTP_200_OK
        job = existing
    else:
        job = models.Job(**payload.model_dump())
        db.add(job)
        db.flush()
        db.commit()
        db.refresh(job)

    _ensure_review_for_user(job.id, user_id, db)
    return job


# ── Internal service endpoint (no auth) ─────────────────────

@router.get("/internal/{job_id}", include_in_schema=False)
def get_job_internal(
    job_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Called by the ai-reviewer worker.  Returns raw job data by Job.id.
    No user auth required — internal service-to-service only.
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── User-facing endpoints (auth required) ────────────────────

@router.get("", response_model=schemas.JobListOut)
def list_jobs(
    job_status: Optional[list[models.JobStatus]] = Query(None, alias="status"),
    source: Optional[list[str]] = Query(None),
    remote_only: Optional[bool] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=10),
    recommended_only: Optional[bool] = Query(None),
    has_contact: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search title or company"),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = (
        db.query(models.UserJobReview)
        .join(models.Job)
        .options(
            joinedload(models.UserJobReview.job),
            joinedload(models.UserJobReview.timeline),
            joinedload(models.UserJobReview.recruiter),
        )
        .filter(models.UserJobReview.user_id == current_user.id)
    )

    # "Known contact" = the job's company matches one of the user's uploaded
    # LinkedIn connections (case-insensitive, trimmed). Computed live so it always
    # reflects the current connections, not a stale stored flag.
    contact_match = exists().where(
        models.LinkedInConnection.user_id == current_user.id,
        models.LinkedInConnection.company.isnot(None),
        func.lower(func.trim(models.LinkedInConnection.company))
        == func.lower(func.trim(models.Job.company)),
    )

    if job_status:
        q = q.filter(models.UserJobReview.status.in_(job_status))
    if source:
        q = q.filter(models.Job.source.in_(source))
    if remote_only is not None:
        q = q.filter(models.Job.remote == remote_only)
    if min_score is not None:
        q = q.filter(models.UserJobReview.ai_score >= min_score)
    if recommended_only:
        q = q.filter(models.UserJobReview.recommended == True)  # noqa: E712
    if has_contact is True:
        q = q.filter(contact_match)
    elif has_contact is False:
        q = q.filter(~contact_match)
    if search:
        term = f"%{search}%"
        q = q.filter(
            (models.Job.title.ilike(term))
            | (models.Job.company.ilike(term))
            | (models.Job.source.ilike(term))
        )

    total = q.count()
    reviews = (
        q.order_by(models.UserJobReview.ai_score.desc().nullslast())
        .offset(skip)
        .limit(limit)
        .all()
    )

    # Normalized set of the user's connection companies — for the contact checkbox.
    conn_companies = {
        row[0]
        for row in db.query(func.lower(func.trim(models.LinkedInConnection.company)))
        .filter(
            models.LinkedInConnection.user_id == current_user.id,
            models.LinkedInConnection.company.isnot(None),
        )
        .distinct()
        .all()
        if row[0]
    }

    items = []
    for r in reviews:
        out = schemas.UserJobReviewOut.from_review(r)
        company_norm = (r.job.company or "").strip().lower()
        out.has_contact = bool(company_norm) and company_norm in conn_companies
        items.append(out)

    return schemas.JobListOut(total=total, items=items)


@router.get("/{review_id}", response_model=schemas.UserJobReviewOut)
def get_job(
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return schemas.UserJobReviewOut.from_review(
        _get_review_or_404(review_id, current_user, db)
    )


@router.patch("/{review_id}", response_model=schemas.UserJobReviewOut)
def update_job(
    review_id: UUID,
    payload: schemas.UserJobReviewUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    review = _get_review_or_404(review_id, current_user, db)
    update_data = payload.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] != review.status:
        old, new = review.status.value, update_data["status"].value
        _add_timeline(db, review.id, "status_change", f"Status: {old} → {new}")
        if update_data["status"] == models.JobStatus.APPLIED and not review.date_applied:
            update_data["date_applied"] = datetime.now(timezone.utc)

    for field, value in update_data.items():
        setattr(review, field, value)

    db.commit()
    db.refresh(review)
    return schemas.UserJobReviewOut.from_review(review)


@router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    User deletes their own job review.  If this was the last review for the
    underlying Job row, that Job is also removed (no orphans).
    """
    review = _get_review_or_404(review_id, current_user, db)
    job_id = review.job_id

    db.delete(review)
    db.flush()   # make the delete visible within this transaction

    remaining = (
        db.query(models.UserJobReview)
        .filter(models.UserJobReview.job_id == job_id)
        .count()
    )
    if remaining == 0:
        db.query(models.Job).filter(models.Job.id == job_id).delete()

    db.commit()


@router.post("/{job_id}/ai-review", response_model=schemas.UserJobReviewOut)
def post_ai_review(
    job_id: UUID,
    payload: schemas.JobAIUpdate,
    user_id: UUID = Query(..., description="User this review belongs to"),
    db: Session = Depends(get_db),
):
    """
    Called by the ai-reviewer Celery worker.  Identified by job_id (the raw
    job) and user_id (whose review to update).  No user auth cookie needed —
    this is an internal service-to-service call.
    """
    review = (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job), joinedload(models.UserJobReview.timeline))
        .filter(
            models.UserJobReview.job_id == job_id,
            models.UserJobReview.user_id == user_id,
        )
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    review.ai_score        = payload.ai_score
    review.ai_summary      = payload.ai_summary
    review.ai_pros         = payload.ai_pros
    review.ai_cons         = payload.ai_cons
    review.skills_rank     = payload.skills_rank
    review.experience_rank = payload.experience_rank
    review.location_rank   = payload.location_rank
    review.education_rank  = payload.education_rank
    review.salary_rank     = payload.salary_rank
    review.recommended     = payload.recommended
    review.ai_reviewed_at  = datetime.now(timezone.utc)

    if review.status == models.JobStatus.NEW:
        review.status = models.JobStatus.REVIEWED

    _add_timeline(db, review.id, "ai_review", f"AI scored {payload.ai_score:.1f}/10")

    db.commit()
    db.refresh(review)
    return schemas.UserJobReviewOut.from_review(review)


@router.post("/manual", response_model=schemas.UserJobReviewOut, status_code=status.HTTP_201_CREATED)
def create_manual_job(
    payload: schemas.JobCreate,
    response: Response,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Called by the authenticated user (via bookmarklet or manual form).
    Creates a Job + UserJobReview for the current user only, then enqueues AI review.
    Returns 200 if the user already has a review for this job.
    """
    # Find existing job: prefer external_id+source match, fall back to URL
    existing_job = None
    if payload.external_id and payload.source:
        existing_job = (
            db.query(models.Job)
            .filter(
                models.Job.external_id == payload.external_id,
                models.Job.source == payload.source,
            )
            .first()
        )
    if not existing_job and payload.url:
        existing_job = (
            db.query(models.Job)
            .filter(models.Job.url == payload.url)
            .first()
        )

    if existing_job:
        # Check if this user already has a review
        existing_review = (
            db.query(models.UserJobReview)
            .options(joinedload(models.UserJobReview.job), joinedload(models.UserJobReview.timeline))
            .filter(
                models.UserJobReview.user_id == current_user.id,
                models.UserJobReview.job_id == existing_job.id,
            )
            .first()
        )
        if existing_review:
            response.status_code = status.HTTP_200_OK
            return schemas.UserJobReviewOut.from_review(existing_review)
        job_uuid = existing_job.id
    else:
        # Create the job
        job = models.Job(**payload.model_dump())
        db.add(job)
        db.flush()
        job_uuid = job.id

    # Create review for this user only
    review = models.UserJobReview(
        user_id=current_user.id,
        job_id=job_uuid,
        status=models.JobStatus.NEW,
    )
    db.add(review)
    db.flush()
    _add_timeline(db, review.id, "status_change", "Job added manually — awaiting AI review")
    db.commit()

    # Reload with relationships for the response
    review = (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job), joinedload(models.UserJobReview.timeline))
        .filter(models.UserJobReview.id == review.id)
        .first()
    )

    # Enqueue AI review for this user only
    _celery.send_task(
        "app.tasks.review_job",
        args=[str(job_uuid), str(current_user.id)],
        queue="review",
    )

    return schemas.UserJobReviewOut.from_review(review)


@router.post("/enqueue-review", status_code=status.HTTP_202_ACCEPTED)
def enqueue_review(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Re-enqueue all of the current user's NEW/unreviewed jobs for AI review.
    Useful after updating criteria or adding a new API key.
    """
    reviews = (
        db.query(models.UserJobReview)
        .filter(
            models.UserJobReview.user_id == current_user.id,
            models.UserJobReview.status == models.JobStatus.NEW,
        )
        .all()
    )
    for r in reviews:
        _celery.send_task(
            "app.tasks.review_job",
            args=[str(r.job_id), str(current_user.id)],
            queue="review",
        )
    return {"enqueued": len(reviews)}


@router.get("/{review_id}/timeline")
def get_timeline(
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    review = _get_review_or_404(review_id, current_user, db)
    return review.timeline


@router.get("/{review_id}/contacts", response_model=list[schemas.LinkedInConnectionOut])
def get_job_contacts(
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The user's LinkedIn connections at this job's company (case-insensitive exact
    match — same rule as the job-list contact checkbox)."""
    review = _get_review_or_404(review_id, current_user, db)
    company = (review.job.company or "").strip()
    if not company:
        return []
    return (
        db.query(models.LinkedInConnection)
        .filter(
            models.LinkedInConnection.user_id == current_user.id,
            func.lower(func.trim(models.LinkedInConnection.company)) == company.lower(),
        )
        .order_by(models.LinkedInConnection.first_name, models.LinkedInConnection.last_name)
        .all()
    )


@router.post("/{review_id}/notes")
def add_note(
    review_id: UUID,
    note: str = Query(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    review = _get_review_or_404(review_id, current_user, db)
    _add_timeline(db, review.id, "note", note)
    db.commit()
    return {"message": "Note added"}
