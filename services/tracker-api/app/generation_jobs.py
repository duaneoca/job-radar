"""Background generation — company research and résumé tailoring.

A research summary with Tavily context, or a tailor pass, takes 30–75 s. Run
inside the request that asked for it, that meant a spinner the user had to
wait on and a race against nginx's 95 s / Cloudflare's 100 s ceilings — which
we lost often enough to raise the nginx timeout once already. Now the request
records a GenerationJob row and returns 202; the work runs after the response
and writes its result to the same review fields it always did
(research_summary / resume_tailor). The page, or an MCP client, polls the row.

Why this runs in the API process and not a Celery worker
---------------------------------------------------------
The obvious home is a worker on the tracker-api image (it has the code and
every library). But that worker would idle at ~220Mi per environment on a
single 3.8Gi node that already goes NotReady on deploys, for work the API
process can do with libraries it has loaded anyway. FastAPI's BackgroundTasks
run the job in the same threadpool the foreground request used, so the memory
and thread cost are exactly what they were before — the only thing that moved
is the HTTP response. The price is durability: a pod restart mid-job kills it.
Two things make that visible rather than silent — the deployment's shutdown
grace covers one full run (k8s/base/tracker-api/deployment.yaml), and a job
still "running" past STALE_AFTER is marked failed on the next read.

If that trade ever flips, `run(job_id)` is already the task body; only the
dispatch in routers/generate.py changes.

Rules the runner enforces, because non-browser clients may not ask first:
  * one active job per user per kind — a second request for the same job
    attaches to the running one; for a different job it is refused (409);
  * the user's key and model are checked in the request (fast, and the user
    is told now), then looked up again at run time — never stored on the row;
  * every finished run leaves a timeline entry naming who asked.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app import models
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# A run that has shown no progress this long is dead — the process restarted
# under it. Twice the longest run seen (parse + tailor, ~150 s), so a slow
# provider is never mistaken for a crash.
STALE_AFTER = timedelta(minutes=5)

STALE_ERROR = ("This didn't finish — the server was restarted while it was running. "
               "Try again.")
UNEXPECTED_ERROR = ("Something went wrong on our side. Try again; if it keeps happening, "
                    "tell the administrator.")

_KIND_LABEL = {
    models.GENERATION_KIND_RESEARCH: "Company research",
    models.GENERATION_KIND_TAILOR: "Résumé tailoring",
}
_DONE_EVENT = {
    models.GENERATION_KIND_RESEARCH: ("research_generated", "Company research generated"),
    models.GENERATION_KIND_TAILOR: ("resume_tailored", "Résumé tailored"),
}

# kind → fn(db, user, review). Registered by routers/generate.py at import so
# this module never imports the router (which imports this module).
Runner = Callable[[Session, models.User, models.UserJobReview], None]
RUNNERS: dict[str, Runner] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; Postgres hands back aware ones.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── Starting ──────────────────────────────────────────────────

def active_job(db: Session, user_id: uuid.UUID, kind: str) -> models.GenerationJob | None:
    return (
        db.query(models.GenerationJob)
        .filter(
            models.GenerationJob.user_id == user_id,
            models.GenerationJob.kind == kind,
            models.GenerationJob.status.in_(models.GENERATION_ACTIVE),
        )
        .order_by(models.GenerationJob.created_at.desc())
        .first()
    )


def start(db: Session, user: models.User, review: models.UserJobReview, kind: str,
          source: str = models.GENERATION_SOURCE_WEB) -> tuple[models.GenerationJob, bool]:
    """Record a request. Returns (job, created): created is False when an active
    run for the same job already exists and the caller should attach to it
    rather than schedule another."""
    assert kind in models.GENERATION_KINDS, kind
    existing = active_job(db, user.id, kind)
    if existing is not None:
        existing = refresh_if_stale(db, existing)
    if existing is not None and existing.status in models.GENERATION_ACTIVE:
        if existing.review_id == review.id:
            return existing, False
        other = (
            db.query(models.Job.title)
            .join(models.UserJobReview, models.UserJobReview.job_id == models.Job.id)
            .filter(models.UserJobReview.id == existing.review_id)
            .scalar()
        )
        raise HTTPException(
            status_code=409,
            detail=(f"{_KIND_LABEL[kind]} is already running for "
                    f"\"{other or 'another job'}\". Wait for it to finish, then try again."),
        )
    job = models.GenerationJob(user_id=user.id, review_id=review.id, kind=kind, source=source)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True


# ── Running ───────────────────────────────────────────────────

def run(job_id: uuid.UUID) -> None:
    """The task body. Opens its own session: the request's session is closed
    by the time a background task runs (FastAPI ≥ 0.106)."""
    db = SessionLocal()
    try:
        job = db.get(models.GenerationJob, job_id)
        if job is None or job.status != models.GENERATION_QUEUED:
            return
        job.status = models.GENERATION_RUNNING
        job.started_at = _utcnow()
        db.commit()

        try:
            review = (
                db.query(models.UserJobReview)
                .options(joinedload(models.UserJobReview.job))
                .filter(models.UserJobReview.id == job.review_id,
                        models.UserJobReview.user_id == job.user_id)
                .first()
            )
            user = db.get(models.User, job.user_id)
            if review is None or user is None:
                raise HTTPException(status_code=404, detail="Job not found")
            RUNNERS[job.kind](db, user, review)
        except HTTPException as exc:
            # The same message the foreground request used to return. These are
            # the user's to fix (dead key, no model, malformed model output…),
            # so WARNING: not an operator fault, must not reach the error digest.
            db.rollback()
            logger.warning("generation %s %s failed for user %s: %s",
                           job.kind, job.id, job.user_id, exc.detail)
            _finish(db, job, models.GENERATION_FAILED, error=str(exc.detail))
            return
        except Exception:
            db.rollback()
            logger.exception("generation %s %s crashed for user %s", job.kind, job.id, job.user_id)
            _finish(db, job, models.GENERATION_FAILED, error=UNEXPECTED_ERROR)
            return

        event_type, text = _DONE_EVENT[job.kind]
        if job.source != models.GENERATION_SOURCE_WEB:
            text += f" via {job.source}"
        db.add(models.TimelineEvent(review_id=job.review_id, event_type=event_type, description=text))
        _finish(db, job, models.GENERATION_DONE)
    finally:
        db.close()


def _finish(db: Session, job: models.GenerationJob, status: str, error: str | None = None) -> None:
    job.status = status
    job.error = error
    job.finished_at = _utcnow()
    db.commit()


# ── Reading ───────────────────────────────────────────────────

def refresh_if_stale(db: Session, job: models.GenerationJob) -> models.GenerationJob:
    """Mark a job failed if it has sat active past STALE_AFTER — the process
    that was running it is gone. Judged on read so no scheduler is needed and
    the row is corrected the moment anyone looks."""
    if job.status not in models.GENERATION_ACTIVE:
        return job
    since = _aware(job.started_at) or _aware(job.created_at)
    if since is not None and _utcnow() - since > STALE_AFTER:
        logger.warning("generation %s %s stale since %s — marking failed", job.kind, job.id, since)
        _finish(db, job, models.GENERATION_FAILED, error=STALE_ERROR)
    return job


def latest_for_review(db: Session, review_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, models.GenerationJob | None]:
    out: dict[str, models.GenerationJob | None] = {k: None for k in models.GENERATION_KINDS}
    for kind in models.GENERATION_KINDS:
        job = (
            db.query(models.GenerationJob)
            .filter(models.GenerationJob.review_id == review_id,
                    models.GenerationJob.user_id == user_id,
                    models.GenerationJob.kind == kind)
            .order_by(models.GenerationJob.created_at.desc())
            .first()
        )
        out[kind] = refresh_if_stale(db, job) if job else None
    return out


def get_for_user(db: Session, job_id: uuid.UUID, user_id: uuid.UUID) -> models.GenerationJob:
    job = db.get(models.GenerationJob, job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="Not found")
    return refresh_if_stale(db, job)


def sweep(db: Session, keep_finished_for: timedelta) -> dict[str, int]:
    """Nightly hygiene (called from admin._do_cleanup): fail anything stale,
    delete finished rows older than keep_finished_for. The rows hold no result
    — that lives on the review — so losing them loses only history."""
    stale = 0
    for job in (
        db.query(models.GenerationJob)
        .filter(models.GenerationJob.status.in_(models.GENERATION_ACTIVE))
        .all()
    ):
        if refresh_if_stale(db, job).status == models.GENERATION_FAILED:
            stale += 1
    cutoff = _utcnow() - keep_finished_for
    deleted = (
        db.query(models.GenerationJob)
        .filter(models.GenerationJob.status.not_in(models.GENERATION_ACTIVE),
                models.GenerationJob.finished_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"generation_jobs_failed_stale": stale, "generation_jobs_deleted": deleted}
