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
    locations: list[str] = criteria.get("locations") or ["Remote"]

    if not keywords:
        logger.warning("Active criteria has no job_titles — skipping.")
        return

    total_created = 0
    total_seen = 0

    for scraper in SCRAPERS:
        for location in locations:
            try:
                raw_jobs = asyncio.run(scraper.scrape(keywords, location))
            except Exception:
                logger.exception("scraper %s crashed", scraper.source_name)
                continue

            total_seen += len(raw_jobs)
            for raw in raw_jobs:
                if _post_job(raw):
                    total_created += 1

    logger.info(
        "scrape_all complete — %d raw jobs seen, %d newly created",
        total_seen, total_created,
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
    """Return the active criteria dict from tracker-api, or None on failure."""
    url = f"{settings.tracker_api_url}/criteria/active"
    try:
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("Failed to fetch active criteria from %s", url)
        return None


def _post_job(raw: RawJob) -> bool:
    """
    POST a RawJob to tracker-api as a JobCreate payload.
    Returns True iff the job was newly created (HTTP 201 with a fresh row).

    Note: tracker-api currently returns 201 for both newly-created and
    already-existing rows, so this only reliably distinguishes the two
    when the API is updated to return 200 for existing. Until then,
    treat the "total_created" counter in scrape_all as "total_posted".
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
        return resp.status_code == 201
    except Exception:
        logger.exception("Failed to post job '%s' (%s)", raw.title, raw.external_id)
        return False
