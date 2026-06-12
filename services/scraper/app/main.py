"""
Scraper Service — Phase 2
-------------------------
Celery worker that queries public jobs APIs on a schedule and pushes
new listings to the tracker-api via HTTP.

Supported sources:
  - Adzuna    (free tier, requires app_id/app_key)
  - The Muse  (public, no auth)
  - Remotive  (public, no auth, remote-only)

HTML scraping was abandoned after Phase 2a because every Cloudflare-
protected board (Indeed, LinkedIn, Glassdoor) blocks datacenter IPs on
first contact. See `memory/project_scraping_strategy.md`.

Run locally:
  celery -A app.main worker --loglevel=info --beat
"""

import asyncio
import logging

import httpx
from celery import Celery
from celery.schedules import crontab

from app.config import settings
from app.scrapers.adzuna import AdzunaScraper
from app.scrapers.base import BaseScraper, RawJob
from app.scrapers.remotive import RemotiveScraper
from app.scrapers.the_muse import TheMuseScraper

logger = logging.getLogger(__name__)

app = Celery("scraper", broker=settings.redis_url, backend=settings.redis_url)

app.conf.beat_schedule = {
    "scrape-all-sources": {
        "task": "app.tasks.scrape_all",
        "schedule": crontab(minute=0, hour="*/2"),  # every 2 hours
    },
    "expire-old-jobs": {
        "task": "app.tasks.expire_jobs",
        "schedule": crontab(minute=45, hour=2),  # 2:45 AM UTC — before cleanup
    },
    "cleanup-old-jobs": {
        "task": "app.tasks.cleanup_jobs",
        "schedule": crontab(minute=0, hour=3),  # 3 AM UTC daily
    },
}

app.conf.timezone = "UTC"


# Registry of enabled scrapers. Order doesn't matter — dedup is handled
# at the tracker-api layer by (source, external_id).
SCRAPERS: list[BaseScraper] = [
    AdzunaScraper(),
    TheMuseScraper(),
    RemotiveScraper(),
]


# ── Tasks ─────────────────────────────────────────────────────

@app.task(name="app.tasks.cleanup_jobs")
def cleanup_jobs():
    """
    Daily maintenance: delete terminal-status reviews (dismissed/rejected/expired)
    older than terminal_ttl_days and remove any now-orphaned job records.
    Delegates to tracker-api which owns the DB.
    """
    url = f"{settings.tracker_api_url}/admin/internal/cleanup"
    try:
        resp = httpx.post(url, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "cleanup_jobs complete — %d reviews deleted, %d orphan jobs deleted",
            result.get("reviews_deleted", 0),
            result.get("orphan_jobs_deleted", 0),
        )
    except Exception:
        logger.exception("cleanup_jobs task failed")


@app.task(name="app.tasks.expire_jobs")
def expire_jobs():
    """
    Daily: soft-expire NEW/REVIEWED reviews unactioned for job_ttl_days, flipping
    them to EXPIRED so the cleanup task sweeps them after terminal_ttl_days.
    Runs before cleanup_jobs. Delegates to tracker-api which owns the DB.
    """
    url = f"{settings.tracker_api_url}/admin/internal/expire"
    try:
        resp = httpx.post(url, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "expire_jobs complete — %d reviews soft-expired",
            result.get("reviews_expired", 0),
        )
    except Exception:
        logger.exception("expire_jobs task failed")


@app.task(name="app.tasks.scrape_all")
def scrape_all():
    """
    1. Fetch the active search criteria from tracker-api.
    2. Run every enabled scraper across each keyword × location combo.
    3. POST new jobs to tracker-api (dedup is handled server-side).
    """
    criteria = _fetch_active_criteria()
    if not criteria:
        logger.warning("No active criteria found — skipping scrape run.")
        return

    keywords: list[str] = criteria.get("job_titles") or []
    locations: list[str] = criteria.get("search_locations") or criteria.get("locations") or ["Remote"]

    if not keywords:
        logger.warning("Active criteria has no job_titles — skipping.")
        return

    total_created = 0
    total_seen = 0
    new_job_ids: list[str] = []

    for scraper in SCRAPERS:
        for location in locations:
            try:
                raw_jobs = asyncio.run(scraper.scrape(keywords, location))
            except Exception:
                logger.exception("scraper %s crashed", scraper.source_name)
                continue

            total_seen += len(raw_jobs)
            for raw in raw_jobs:
                job_id = _post_job(raw)
                if job_id:
                    total_created += 1
                    new_job_ids.append(job_id)

    # Enqueue each new job for AI review
    for job_id in new_job_ids:
        app.send_task("app.tasks.review_job", args=[job_id], queue="review")
        logger.debug("Enqueued review for job %s", job_id)

    logger.info(
        "scrape_all complete — %d raw jobs seen, %d newly created, %d enqueued for review",
        total_seen, total_created, len(new_job_ids),
    )


# ── Helpers ───────────────────────────────────────────────────

def _clip(value: str | None, max_len: int) -> str | None:
    """Truncate overlong strings to fit tracker-api's DB column limits."""
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def _fetch_active_criteria() -> dict | None:
    """Return merged criteria from all approved users via the public union endpoint."""
    url = f"{settings.tracker_api_url}/criteria/scraper/union"
    try:
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("Failed to fetch active criteria from %s", url)
        return None


def _post_job(raw: RawJob) -> str | None:
    """
    POST a RawJob to tracker-api as a JobCreate payload.
    Returns the job ID (str) if the job was newly created (HTTP 201),
    or None if it already existed (HTTP 200) or on error.
    """
    payload = {
        "external_id": _clip(raw.external_id, 255),
        "source": raw.source,
        "title": _clip(raw.title, 255),
        "company": _clip(raw.company, 255),
        "url": _clip(raw.url, 2048),
        "location": _clip(raw.location, 255),
        "remote": raw.remote,
        "description": raw.description,   # TEXT column, no clip needed
        "salary_min": raw.salary_min,
        "salary_max": raw.salary_max,
        "date_posted": raw.date_posted,
    }
    # Strip None values so we don't overwrite server defaults.
    payload = {k: v for k, v in payload.items() if v is not None}

    url = f"{settings.tracker_api_url}/jobs"
    try:
        resp = httpx.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        if resp.status_code == 201:
            return resp.json()["id"]
        return None  # 200 = already existed, skip review
    except Exception:
        logger.exception("Failed to post job '%s' (%s)", raw.title, raw.external_id)
        return None
