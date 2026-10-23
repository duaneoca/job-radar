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
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import exists, func
from sqlalchemy.orm import Session, joinedload

from app import assistant, dedup, models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_internal_token

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


def _user_review(job_id: UUID, user_id: UUID, db: Session) -> "models.UserJobReview | None":
    return db.query(models.UserJobReview).filter(
        models.UserJobReview.user_id == user_id,
        models.UserJobReview.job_id == job_id,
    ).first()


def _listing_is_recent(payload: schemas.JobCreate) -> bool:
    """Whether a listing is young enough that the posting-age rule
    (posting_max_age_days) wouldn't expire it again tonight."""
    posted = payload.date_posted
    if posted is None:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - posted <= timedelta(days=settings.posting_max_age_days)


def _seen_again(job: models.Job, review: "models.UserJobReview | None",
                payload: schemas.JobCreate, db: Session) -> None:
    """A job the user already has was listed again. Make the one entry current.

    Aggregators re-post an opening under a new id every few days and the old
    id's link dies (production: one ServiceNow role had four Adzuna ids in a
    month) — so keeping the first copy's link is how users kept clicking dead
    ads. When the SAME source re-lists it, the job takes the new link, dates,
    text and salary. A copy from another source leaves the link alone: an
    employer's careers page or company board beats an aggregator redirect.
    Either way `date_scraped` records that it was just seen.

    A review the SYSTEM expired comes back, with a timeline note — the role is
    being advertised again. Only when the new listing is itself recent, or the
    posting-age rule would expire it again tonight. Dismissed stays dismissed:
    that was the user's call.
    """
    source = getattr(payload.source, "value", payload.source)
    job.date_scraped = datetime.now(timezone.utc)
    if job.source == source:
        job.url = payload.url
        if payload.date_posted is not None:
            job.date_posted = payload.date_posted
        if payload.description:
            job.description = payload.description
        if payload.salary_min is not None:
            job.salary_min = payload.salary_min
        if payload.salary_max is not None:
            job.salary_max = payload.salary_max
    if (review is not None and review.status == models.JobStatus.EXPIRED
            and _listing_is_recent(payload)):
        review.status = (models.JobStatus.REVIEWED if review.ai_score is not None
                         else models.JobStatus.NEW)
        _add_timeline(db, review.id, "status_change",
                      f"Listed again on {source} — back in your list")
    db.commit()


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
    _it: None = Depends(require_internal_token),
):
    """
    Called by the per-user scraper. Deduplicates the shared Job by
    (external_id, source), then attributes it to `user_id` only — creates that
    user's review + enqueues their AI review. No fan-out to other users.

    Returns 201 for a newly created Job, 200 if the Job already existed (the
    user still gets their own review either way).
    """
    key = dedup.match_key(payload.company, payload.title, payload.location)
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

    # The same listing again (same external id) for a user who already has it:
    # nothing new to create, but it was just seen — refresh it (_seen_again).
    if existing is not None:
        mine = _user_review(existing.id, user_id, db)
        if mine is not None:
            _seen_again(existing, mine, payload, db)
            response.status_code = status.HTTP_200_OK
            return existing

    # A re-listing of a job this user already has (same company, title, city):
    # no new Job row, review or AI scoring at their expense — the existing entry
    # takes the fresh listing instead (_seen_again). Aggregators only; see
    # app/dedup.py. The freshest twin wins if the user somehow has several.
    if key and payload.source in dedup.AGGREGATOR_SOURCES:
        twin = (
            db.query(models.Job)
            .join(models.UserJobReview, models.UserJobReview.job_id == models.Job.id)
            .filter(models.UserJobReview.user_id == user_id, models.Job.match_key == key)
            .order_by(models.Job.date_scraped.desc())
            .first()
        )
        if twin is not None:
            _seen_again(twin, _user_review(twin.id, user_id, db), payload, db)
            response.status_code = status.HTTP_200_OK
            return twin

    if existing:
        response.status_code = status.HTTP_200_OK
        job = existing
    else:
        job = models.Job(**payload.model_dump(), match_key=key)
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
    _it: None = Depends(require_internal_token),
):
    """
    Called by the ai-reviewer worker.  Returns raw job data by Job.id.
    No user auth required — internal service-to-service only.
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/board-sync", include_in_schema=False)
def board_sync(
    payload: schemas.BoardSyncIn,
    user_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _it: None = Depends(require_internal_token),
):
    """Expire company-board postings the employer has taken down.

    The scraper sends every external_id still on each board it SUCCESSFULLY read.
    A posting we hold that is not in that set has been removed — company boards
    return every open role, so absence is real evidence here (unlike an
    aggregator search, which returns a ranked, truncated slice).

    Two safety properties matter more than the feature:
      * only boards that returned a valid payload are ever sent, so a 429 or a
        dead slug can never masquerade as "the board is empty";
      * a posting must be missing BOARD_MISS_THRESHOLD scrapes in a row, so one
        bad run cannot clear a company. Reappearing resets the counter.

    Expiry is global for the job (board presence is a property of the posting,
    not of one user) and touches only NEW/REVIEWED — anything you have acted on
    is left alone. `user_id` is for log context only.
    """
    if payload.source not in models.BOARD_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"board-sync is only valid for {', '.join(models.BOARD_SOURCES)}",
        )

    now = datetime.now(timezone.utc)
    missing = expired = reset = 0

    for company, ids in payload.companies.items():
        live = set(ids)
        jobs = (
            db.query(models.Job)
            .filter(models.Job.source == payload.source, models.Job.company == company)
            .all()
        )
        for job in jobs:
            if job.external_id in live:
                if job.board_missing_count:
                    job.board_missing_count = 0
                    reset += 1
                continue
            job.board_missing_count = (job.board_missing_count or 0) + 1
            missing += 1
            if job.board_missing_count < models.BOARD_MISS_THRESHOLD:
                continue
            # Starred reviews expire here too, unlike in _do_expire: this is
            # evidence the employer took the posting down, not a guess, and the
            # user would want to know. Cleanup still never deletes them.
            expired += (
                db.query(models.UserJobReview)
                .filter(
                    models.UserJobReview.job_id == job.id,
                    models.UserJobReview.status.in_(
                        [models.JobStatus.NEW, models.JobStatus.REVIEWED]
                    ),
                )
                .update(
                    {
                        models.UserJobReview.status: models.JobStatus.EXPIRED,
                        models.UserJobReview.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )

    db.commit()
    logger.info(
        "board-sync %s (user %s): %d companies, %d missing, %d expired, %d back on board",
        payload.source, user_id, len(payload.companies), missing, expired, reset,
    )
    return {"missing": missing, "expired": expired, "reset": reset}


# ── User-facing endpoints (auth required) ────────────────────

@router.get("", response_model=schemas.JobListOut)
def list_jobs(
    job_status: Optional[list[models.JobStatus]] = Query(None, alias="status"),
    source: Optional[list[str]] = Query(None),
    remote_only: Optional[bool] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=10),
    recommended_only: Optional[bool] = Query(None),
    has_contact: Optional[bool] = Query(None),
    starred: Optional[bool] = Query(None),
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
            # No timeline: the list shape omits it (see UserJobReviewOut.from_review).
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
    if starred is not None:
        q = q.filter(models.UserJobReview.starred == starred)
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
        out = schemas.UserJobReviewOut.from_review(r, summary=True)
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    review = _get_review_or_404(review_id, current_user, db)
    update_data = payload.model_dump(exclude_unset=True)
    reason = (update_data.pop("status_reason", None) or "").strip()
    by_app = assistant.is_assistant(request)
    if by_app:
        # Connected apps: star / unstar, expire or dismiss with a reason — and
        # nothing else, whatever the client sends (app/assistant.py).
        assistant.guard_patch({**update_data, **({"status_reason": reason} if reason else {})})

    if "status" in update_data and update_data["status"] != review.status:
        if by_app:
            assistant.check(db, current_user.id, assistant.ACTION_REMOVE)
            assistant.record(db, request, current_user.id, assistant.ACTION_REMOVE, review.id)
        old, new = review.status.value, update_data["status"].value
        text = f"Status: {old} → {new}"
        if by_app:
            text += f" — by {assistant.who(request)}"
        if reason:
            text += f": {reason}"
        _add_timeline(db, review.id, "status_change", text)
        if update_data["status"] == models.JobStatus.APPLIED and not review.date_applied:
            update_data["date_applied"] = datetime.now(timezone.utc)

    if by_app and "starred" in update_data and bool(update_data["starred"]) != bool(review.starred):
        _add_timeline(db, review.id, "note",
                      f"{'Starred' if update_data['starred'] else 'Star removed'} by {assistant.who(request)}")

    for field, value in update_data.items():
        setattr(review, field, value)

    db.commit()
    db.refresh(review)
    return schemas.UserJobReviewOut.from_review(review)


@router.post("/{review_id}/notes", response_model=schemas.UserJobReviewOut)
def append_note(
    review_id: UUID,
    payload: schemas.NoteAppendIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add to a job's notes without replacing them — the only way a connected app
    can write notes, so it can never wipe the user's own."""
    review = _get_review_or_404(review_id, current_user, db)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_app = assistant.is_assistant(request)
    header = f"[{stamp}{' · ' + assistant.who(request) if by_app else ''}]"
    entry = f"{header} {payload.text.strip()}"
    review.notes = f"{review.notes.rstrip()}\n\n{entry}" if (review.notes or "").strip() else entry
    if by_app:
        _add_timeline(db, review.id, "note", f"Note added by {assistant.who(request)}")
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
    _it: None = Depends(require_internal_token),
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


def _manual_twin(payload: schemas.JobCreate, user_id: UUID, db: Session):
    """The user's review of the same role (company, title, city), freshest first."""
    key = dedup.match_key(payload.company, payload.title, payload.location)
    if not key:
        return None
    return (
        db.query(models.UserJobReview)
        .join(models.Job, models.Job.id == models.UserJobReview.job_id)
        .filter(models.UserJobReview.user_id == user_id, models.Job.match_key == key)
        .order_by(models.Job.date_scraped.desc())
        .first()
    )


def _adopt_imported_link(review: models.UserJobReview, payload: schemas.JobCreate,
                         db: Session) -> models.UserJobReview:
    """The link the user imported replaces an AGGREGATOR copy's: they chose it.

    On a new Job row of their own, never by editing the old one — Job rows are
    shared, and another user's entry must not change under them. The old row is
    left to the nightly orphan cleanup if nobody else has it. A company-board
    copy keeps its link: that is already the best one.
    """
    job = review.job
    if (not payload.url or job.source not in dedup.AGGREGATOR_SOURCES
            or job.url == payload.url):
        return review
    mine = models.Job(**payload.model_dump(),
                      match_key=dedup.match_key(payload.company, payload.title, payload.location))
    db.add(mine)
    db.flush()
    review.job_id = mine.id
    _add_timeline(db, review.id, "note", "Link updated to the one you imported")
    db.commit()
    return (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job), joinedload(models.UserJobReview.timeline))
        .filter(models.UserJobReview.id == review.id)
        .first()
    )


@router.post("/manual", response_model=schemas.UserJobReviewOut, status_code=status.HTTP_201_CREATED)
def create_manual_job(
    payload: schemas.JobCreate,
    response: Response,
    separate: bool = Query(False, description="Add even if the user has this role already"),
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
            out = schemas.UserJobReviewOut.from_review(existing_review)
            out.already_had = "same_link"
            return out
        job_uuid = existing_job.id
    elif not separate and (twin := _manual_twin(payload, current_user.id, db)) is not None:
        # Same company, title and city as a job the user already has — from
        # another listing (an inbox link vs. the Adzuna copy). Don't add a second
        # copy or score it again; return theirs and let the page offer "add as
        # a separate job" for genuinely separate openings.
        response.status_code = status.HTTP_200_OK
        out = schemas.UserJobReviewOut.from_review(_adopt_imported_link(twin, payload, db))
        out.already_had = "same_role"
        return out
    else:
        # Create the job — with a match key, so later aggregator copies of it are
        # recognised (a manual import itself is never skipped as a copy).
        job = models.Job(**payload.model_dump(),
                         match_key=dedup.match_key(payload.company, payload.title, payload.location))
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
