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
from app.reviewer import JobReviewer

logger = logging.getLogger(__name__)

app = Celery("ai-reviewer", broker=settings.redis_url, backend=settings.redis_url)


@app.task(name="app.tasks.review_job", queue="review", bind=True, max_retries=3)
def review_job(self, job_id: str, user_id: str):
    """
    Fetch a job and the user's criteria/profile/API-key from tracker-api,
    score it with Claude, post the result back.
    """
    base = settings.tracker_api_url

    # 1. Fetch the job (internal endpoint — no auth required)
    try:
        resp = httpx.get(f"{base}/jobs/internal/{job_id}", timeout=10)
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
                         headers={"X-Internal-User-Id": user_id})
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
                         headers={"X-Internal-User-Id": user_id})
        profile = resp.json() if resp.status_code == 200 else {}
    except Exception as exc:
        logger.warning("Could not fetch profile for user %s: %s", user_id, exc)
        profile = {}

    # 4. Fetch user's best available LLM key
    try:
        resp = httpx.get(f"{base}/keys/internal/{user_id}/llm", timeout=10)
        if resp.status_code == 404:
            logger.warning("No AI key configured for user %s — skipping review", user_id)
            return
        resp.raise_for_status()
        key_data = resp.json()
        api_key = key_data["api_key"]
        model = key_data["model"]
    except Exception as exc:
        logger.exception("Failed to fetch API key for user %s", user_id)
        raise self.retry(exc=exc, countdown=30)

    # 5. Score the job
    reviewer = JobReviewer(api_key=api_key, model=model)
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

    if result is None:
        logger.error("Review returned None for job %s / user %s", job_id, user_id)
        return

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
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Posted review for job %s / user %s — score %.1f",
                    job_id, user_id, result.score)
    except Exception as exc:
        logger.exception("Failed to post review for job %s / user %s", job_id, user_id)
        raise self.retry(exc=exc, countdown=30)
