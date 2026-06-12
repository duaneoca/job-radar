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
        "schedule": crontab(minute=0, hour="*/6"),  # every 6 hours
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
    """Scheduled scrape. Per-user (BYOK) by default; union mode behind a toggle.

    tracker-api owns review creation + AI-review enqueue (per-user via the
    user_id attribution, or fan-out in union mode), so the scraper only POSTs.
    """
    if settings.per_user_scraping:
        _scrape_all_per_user()
    else:
        _scrape_all_union()


@app.task(name="app.tasks.scrape_user")
def scrape_user(user_id: str):
    """Scrape a single user's criteria immediately (e.g. after they edit criteria)."""
    if not settings.per_user_scraping:
        logger.info("scrape_user ignored — per-user scraping disabled")
        return
    configs = _fetch_user_configs() or []
    cfg = next((c for c in configs if str(c.get("user_id")) == str(user_id)), None)
    if not cfg:
        logger.warning("scrape_user: no active config for user %s", user_id)
        return
    seen, created = _scrape_for_config(cfg)
    logger.info("scrape_user %s complete — %d seen, %d created", user_id, seen, created)


def _scrape_all_per_user():
    configs = _fetch_user_configs()
    if not configs:
        logger.warning("No user configs found — skipping scrape run.")
        return

    total_seen = total_created = 0
    for cfg in configs:
        try:
            seen, created = _scrape_for_config(cfg)
            total_seen += seen
            total_created += created
        except Exception:
            logger.exception("scrape failed for user %s", cfg.get("user_id"))

    logger.info(
        "scrape_all (per-user) complete — %d users, %d raw seen, %d newly created",
        len(configs), total_seen, total_created,
    )


def _scrape_for_config(cfg: dict) -> tuple[int, int]:
    """Scrape one user's criteria with their own Adzuna key. Returns (seen, created)."""
    user_id = cfg.get("user_id")
    keywords: list[str] = cfg.get("job_titles") or []
    if not keywords:
        return 0, 0
    locations = _ensure_remote_pass(cfg.get("search_locations") or ["Remote"])
    adzuna_creds = cfg.get("adzuna")  # {app_id, app_key} or None

    seen = created = 0
    for scraper in SCRAPERS:
        # Adzuna is BYOK — skip entirely for users without a key.
        if scraper.source_name == "adzuna" and not adzuna_creds:
            continue
        creds = adzuna_creds if scraper.source_name == "adzuna" else None
        for location in locations:
            try:
                raw_jobs = asyncio.run(scraper.scrape(keywords, location, creds))
            except Exception:
                logger.exception("scraper %s crashed for user %s", scraper.source_name, user_id)
                continue
            seen += len(raw_jobs)
            for raw in raw_jobs:
                if _post_job(raw, user_id=user_id):
                    created += 1
    return seen, created


def _scrape_all_union():
    """Legacy union scrape — one shared global Adzuna key, fanned out to all users."""
    criteria = _fetch_active_criteria()
    if not criteria:
        logger.warning("No active criteria found — skipping scrape run.")
        return

    keywords: list[str] = criteria.get("job_titles") or []
    locations: list[str] = criteria.get("search_locations") or criteria.get("locations") or ["Remote"]
    locations = _ensure_remote_pass(locations)

    if not keywords:
        logger.warning("Active criteria has no job_titles — skipping.")
        return

    total_seen = total_created = 0
    for scraper in SCRAPERS:
        for location in locations:
            try:
                raw_jobs = asyncio.run(scraper.scrape(keywords, location))
            except Exception:
                logger.exception("scraper %s crashed", scraper.source_name)
                continue
            total_seen += len(raw_jobs)
            for raw in raw_jobs:
                if _post_job(raw):   # no user_id → server-side fan-out + enqueue
                    total_created += 1

    logger.info(
        "scrape_all (union) complete — %d raw jobs seen, %d newly created",
        total_seen, total_created,
    )


# ── Helpers ───────────────────────────────────────────────────

_REMOTE_ALIASES = {"remote", "anywhere"}


def _ensure_remote_pass(locations: list[str]) -> list[str]:
    """Guarantee the scrape loop runs at least one remote pass.

    Remote-only sources (Remotive) and remote Adzuna results only run when the
    location is remote/anywhere. If the user only listed concrete cities, append
    a "Remote" pass so those sources aren't silently skipped. Idempotent.
    """
    if any((loc or "").strip().lower() in _REMOTE_ALIASES for loc in locations):
        return locations
    return [*locations, "Remote"]


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


def _fetch_user_configs() -> list[dict] | None:
    """Return per-user scrape configs (criteria + decrypted Adzuna creds)."""
    url = f"{settings.tracker_api_url}/criteria/scraper/user-configs"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("Failed to fetch user configs from %s", url)
        return None


def _post_job(raw: RawJob, user_id: str | None = None) -> str | None:
    """
    POST a RawJob to tracker-api as a JobCreate payload.

    With `user_id`, the job is attributed to that user only (per-user BYOK);
    without it, tracker-api fans the job out to all users (union mode).

    Returns the job ID (str) if the Job was newly created (HTTP 201),
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
    params = {"user_id": user_id} if user_id else None
    try:
        resp = httpx.post(url, json=payload, params=params, timeout=15)
        resp.raise_for_status()
        if resp.status_code == 201:
            return resp.json()["id"]
        return None  # 200 = job already existed
    except Exception:
        logger.exception("Failed to post job '%s' (%s)", raw.title, raw.external_id)
        return None
