"""
AI Reviewer Service — Phase 3
------------------------------
Celery worker that listens for new jobs on the review queue,
scores them against the user's criteria using the Claude API,
and posts results back to the tracker-api.

Flow:
  1. tracker-api places new job IDs on the 'review' Redis queue
  2. This worker picks them up, fetches full job details
  3. Calls Claude API with job description + user criteria
  4. Posts score + summary back to tracker-api
"""

from celery import Celery
from app.config import settings

app = Celery("ai-reviewer", broker=settings.redis_url, backend=settings.redis_url)


@app.task(name="app.tasks.review_job", queue="review")
def review_job(job_id: str):
    """Score a single job against user criteria. Implemented in Phase 3."""
    # TODO (Phase 3): call Claude API, post score to tracker-api
    pass
