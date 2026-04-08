"""
Jobs router — CRUD + status management for job postings.
"""

from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Helpers ──────────────────────────────────────────────────

def get_job_or_404(job_id: UUID, db: Session) -> models.Job:
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def add_timeline_event(db: Session, job_id: UUID, event_type: str, description: str):
    event = models.TimelineEvent(
        job_id=job_id,
        event_type=event_type,
        description=description,
    )
    db.add(event)


# ── Endpoints ────────────────────────────────────────────────

@router.get("", response_model=schemas.JobListOut)
def list_jobs(
    status: Optional[models.JobStatus] = Query(None, description="Filter by status"),
    source: Optional[models.JobSource] = Query(None, description="Filter by source"),
    remote_only: Optional[bool] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=10),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List jobs with optional filters and pagination."""
    q = db.query(models.Job)

    if status:
        q = q.filter(models.Job.status == status)
    if source:
        q = q.filter(models.Job.source == source)
    if remote_only is not None:
        q = q.filter(models.Job.remote == remote_only)
    if min_score is not None:
        q = q.filter(models.Job.ai_score >= min_score)

    total = q.count()
    items = q.order_by(models.Job.date_scraped.desc()).offset(skip).limit(limit).all()

    return schemas.JobListOut(total=total, items=items)


@router.post("", response_model=schemas.JobOut, status_code=status.HTTP_201_CREATED)
def create_job(payload: schemas.JobCreate, db: Session = Depends(get_db)):
    """Create a new job posting (manual entry or from scraper)."""
    # Deduplicate by external_id + source
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
            return existing

    job = models.Job(**payload.model_dump())
    db.add(job)
    db.flush()

    add_timeline_event(db, job.id, "status_change", f"Job added with status: {job.status.value}")
    db.commit()
    db.refresh(job)
    return job


@router.get("/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    """Get a single job by ID."""
    return get_job_or_404(job_id, db)


@router.patch("/{job_id}", response_model=schemas.JobOut)
def update_job(job_id: UUID, payload: schemas.JobUpdate, db: Session = Depends(get_db)):
    """Update job fields. Automatically records status changes in the timeline."""
    job = get_job_or_404(job_id, db)

    update_data = payload.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] != job.status:
        old_status = job.status.value
        new_status = update_data["status"].value
        add_timeline_event(
            db, job.id, "status_change",
            f"Status changed: {old_status} → {new_status}"
        )
        # Auto-set date_applied when marking as applied
        if update_data["status"] == models.JobStatus.APPLIED and not job.date_applied:
            update_data["date_applied"] = datetime.now(timezone.utc)

    for field, value in update_data.items():
        setattr(job, field, value)

    db.commit()
    db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: UUID, db: Session = Depends(get_db)):
    """Permanently delete a job record."""
    job = get_job_or_404(job_id, db)
    db.delete(job)
    db.commit()


@router.post("/{job_id}/ai-review", response_model=schemas.JobOut)
def update_ai_review(
    job_id: UUID,
    payload: schemas.JobAIUpdate,
    db: Session = Depends(get_db),
):
    """Called by the ai-reviewer service to post AI score and summary."""
    job = get_job_or_404(job_id, db)

    job.ai_score = payload.ai_score
    job.ai_summary = payload.ai_summary
    job.ai_pros = payload.ai_pros
    job.ai_cons = payload.ai_cons
    job.ai_reviewed_at = datetime.now(timezone.utc)

    if job.status == models.JobStatus.NEW:
        job.status = models.JobStatus.REVIEWED

    add_timeline_event(
        db, job.id, "ai_review",
        f"AI scored job {payload.ai_score:.1f}/10"
    )

    db.commit()
    db.refresh(job)
    return job


@router.get("/{job_id}/timeline", response_model=List[schemas.TimelineEventOut])
def get_timeline(job_id: UUID, db: Session = Depends(get_db)):
    """Get the full timeline of events for a job."""
    get_job_or_404(job_id, db)
    return (
        db.query(models.TimelineEvent)
        .filter(models.TimelineEvent.job_id == job_id)
        .order_by(models.TimelineEvent.occurred_at)
        .all()
    )


@router.post("/{job_id}/notes", response_model=schemas.JobOut)
def add_note(
    job_id: UUID,
    note: str = Query(..., description="Note text"),
    db: Session = Depends(get_db),
):
    """Add a freeform note to a job, recorded in the timeline."""
    job = get_job_or_404(job_id, db)
    add_timeline_event(db, job.id, "note", note)
    db.commit()
    db.refresh(job)
    return job
