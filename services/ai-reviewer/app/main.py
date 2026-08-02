"""
AI Reviewer Service
-------------------
Celery worker that scores jobs against each user's criteria/profile
using their own Anthropic API key (BYOK).

Task signature: review_job(job_id: str, user_id: str)
"""

import logging

import httpx
from celery import Celery

from app.config import settings
from app.llm_errors import PROVIDER_UNAVAILABLE, LLMCallFailed
from app.logging_config import configure_logging
from app.reviewer import JobReviewer

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = Celery("ai-reviewer", broker=settings.redis_url, backend=settings.redis_url)
# Keep our format. Celery replaces the root logger's handlers on worker startup
# unless told not to, which would leave the digest parsing a format that no
# longer exists — the quiet way this feature ships looking fine and doing nothing.
app.conf.worker_hijack_root_logger = False


def _internal_headers(user_id: str | None = None) -> dict:
    """Auth headers for internal tracker-api calls: X-Internal-Token when
    configured (tracker-api enforces it in a later phase), plus X-Internal-User-Id
    when acting on behalf of a specific user."""
    h: dict[str, str] = {}
    if settings.agent_internal_token:
        h["X-Internal-Token"] = settings.agent_internal_token
    if user_id:
        h["X-Internal-User-Id"] = user_id
    return h


def _report_key_status(base: str, user_id: str, kind: str | None, detail: str = "") -> None:
    """Tell tracker-api how the user's LLM key behaved, so the UI can show it.

    Fire-and-forget: a failure to report must never change the outcome of the
    review task itself.
    """
    try:
        httpx.post(
            f"{base}/keys/internal/{user_id}/llm/status",
            json={"kind": kind, "detail": detail[:1000]},
            headers=_internal_headers(),
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        logger.warning("Could not report key status for user %s: %s", user_id, exc)


@app.task(name="app.tasks.review_job", queue="review", bind=True, max_retries=3)
def review_job(self, job_id: str, user_id: str):
    """
    Fetch a job and the user's criteria/profile/API-key from tracker-api,
    score it with Claude, post the result back.
    """
    base = settings.tracker_api_url

    # 1. Fetch the job (internal endpoint — no auth required)
    try:
        resp = httpx.get(f"{base}/jobs/internal/{job_id}", timeout=10,
                         headers=_internal_headers())
        if resp.status_code == 404:
            logger.warning("Job %s not found — skipping", job_id)
            return
        resp.raise_for_status()
        job = resp.json()
    except Exception as exc:
        logger.exception("Failed to fetch job %s", job_id)
        raise self.retry(exc=exc, countdown=30)

    # 2. Fetch user's active criteria
    try:
        resp = httpx.get(f"{base}/criteria/active", timeout=10,
                         headers=_internal_headers(user_id))
        if resp.status_code == 404:
            logger.warning("No active criteria for user %s — skipping", user_id)
            return
        resp.raise_for_status()
        criteria = resp.json()
    except Exception as exc:
        logger.exception("Failed to fetch criteria for user %s", user_id)
        raise self.retry(exc=exc, countdown=30)

    # 3. Fetch user's active profile (optional — degrade gracefully)
    try:
        resp = httpx.get(f"{base}/profile/active", timeout=10,
                         headers=_internal_headers(user_id))
        profile = resp.json() if resp.status_code == 200 else {}
    except Exception as exc:
        logger.warning("Could not fetch profile for user %s: %s", user_id, exc)
        profile = {}

    # 4. Fetch user's best available LLM key
    try:
        resp = httpx.get(f"{base}/keys/internal/{user_id}/llm", timeout=10,
                         headers=_internal_headers())
        if resp.status_code == 404:
            logger.warning("No AI key configured for user %s — skipping review", user_id)
            return
        if resp.status_code == 409:
            # The user has a key but has not chosen a model. There is no default,
            # and the banner already tells them — nothing to retry, nothing to
            # escalate to the operator.
            logger.warning("No model selected for user %s — skipping review", user_id)
            return
        resp.raise_for_status()
        key_data = resp.json()
        api_key = key_data["api_key"]
        model = key_data["model"]
        had_error = bool(key_data.get("last_error_kind"))
    except Exception as exc:
        logger.exception("Failed to fetch API key for user %s", user_id)
        raise self.retry(exc=exc, countdown=30)

    # 5. Score the job
    reviewer = JobReviewer(api_key=api_key, model=model)
    try:
        result = reviewer.review(
            job_id=job_id,
            job_title=job["title"],
            company=job["company"],
            description=job.get("description", ""),
            location=job.get("location"),
            remote=job.get("remote", False),
            salary_min=job.get("salary_min"),
            salary_max=job.get("salary_max"),
            criteria=criteria,
            profile=profile,
        )
    except LLMCallFailed as exc:
        if exc.permanent:
            # The provider rejected the key or the model. Retrying would fail
            # identically for every remaining job in the queue; record it once so
            # the user sees a banner, and stop.
            _report_key_status(base, user_id, exc.kind, exc.message)
            logger.warning(
                "Permanent LLM failure (%s) for user %s — not retrying job %s",
                exc.kind, user_id, job_id,
            )
            return

        if self.request.retries >= self.max_retries:
            # Transient, but it has outlasted every retry — roughly three
            # minutes. Either the user is against a quota ceiling or their
            # provider is down; both are theirs to see and neither is anything
            # the operator can fix, so report it and stop.
            #
            # reviewer.py decided which flavour while it still had the original
            # exception. Getting this wrong means confidently telling someone the
            # wrong cause — "you are being rate-limited" during an outage sends
            # them to change a quota that was never the problem.
            #
            # Returning here also keeps the exception from escaping the task.
            # Celery re-raises the original exc once retries are exhausted and
            # logs it at ERROR, which would put one user's provider trouble in
            # the operator's hourly digest.
            kind = exc.transient or PROVIDER_UNAVAILABLE
            _report_key_status(base, user_id, kind, exc.message)
            logger.warning(
                "Transient LLM failure (%s) survived %d retries for user %s — "
                "job %s not scored",
                kind, self.request.retries, user_id, job_id,
            )
            return

        raise self.retry(exc=exc, countdown=60)

    if result is None:
        # The call succeeded; the model's answer was unusable. That is a scoring
        # bug or a prompt problem, not a key problem — and it is the operator's
        # to look at, so it stays at ERROR. reviewer.py already logged the detail.
        logger.error("Review returned None for job %s / user %s", job_id, user_id)
        return

    # The key works. Only post back if there was actually a failure recorded —
    # otherwise every review in a large backlog would make a pointless round trip.
    if had_error:
        _report_key_status(base, user_id, None)

    # 6. Post the result back
    payload = {
        "ai_score": result.score,
        "ai_summary": result.summary,
        "ai_pros": result.pros,
        "ai_cons": result.cons,
        "skills_rank": result.skills_rank,
        "experience_rank": result.experience_rank,
        "location_rank": result.location_rank,
        "education_rank": result.education_rank,
        "salary_rank": result.salary_rank,
        "recommended": result.recommended,
    }
    try:
        resp = httpx.post(
            f"{base}/jobs/{job_id}/ai-review",
            json=payload,
            params={"user_id": user_id},
            headers=_internal_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Posted review for job %s / user %s — score %.1f",
                    job_id, user_id, result.score)
    except Exception as exc:
        logger.exception("Failed to post review for job %s / user %s", job_id, user_id)
        raise self.retry(exc=exc, countdown=30)
