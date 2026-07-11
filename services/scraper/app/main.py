"""
Scraper Service — Phase 2
-------------------------
Celery worker that queries public jobs APIs on a schedule and pushes
new listings to the tracker-api via HTTP.

Supported sources:
  - Adzuna       (free tier, requires app_id/app_key — BYOK per user)
  - The Muse     (public, no auth)
  - Remotive     (public, no auth, remote-only)
  - JSearch      (RapidAPI, BYOK per user — Google-for-Jobs index, hard
                 monthly quota so heavily budgeted: see scrapers/jsearch.py)
  - ATS boards   (public, no auth): Greenhouse / Ashby / Lever — watch the
                 user's target_companies directly, prefiltered by job titles

HTML scraping was abandoned after Phase 2a because every Cloudflare-
protected board (Indeed, LinkedIn, Glassdoor) blocks datacenter IPs on
first contact. The ATS board endpoints are official public JSON APIs,
consistent with that stance. See `memory/project_scraping_strategy.md`.

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
from app.scrapers.ats_boards import AshbyScraper, GreenhouseScraper, LeverScraper
from app.scrapers.base import BaseScraper, CompanyBoardScraper, Creds, RawJob
from app.scrapers.jsearch import JSearchScraper
from app.scrapers.remotive import RemotiveScraper
from app.scrapers.the_muse import TheMuseScraper

logger = logging.getLogger(__name__)

app = Celery("scraper", broker=settings.redis_url, backend=settings.redis_url)


def _internal_headers() -> dict:
    """X-Internal-Token for internal tracker-api calls when configured
    (tracker-api enforces it in a later phase; empty = header omitted)."""
    if settings.agent_internal_token:
        return {"X-Internal-Token": settings.agent_internal_token}
    return {}

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
    JSearchScraper(),
]

# Company-board watchers: run once per user against their target_companies
# (no location dimension — a board is a board). Same dedup as above.
COMPANY_SCRAPERS: list[CompanyBoardScraper] = [
    GreenhouseScraper(),
    AshbyScraper(),
    LeverScraper(),
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
        resp = httpx.post(url, timeout=30, headers=_internal_headers())
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
        resp = httpx.post(url, timeout=30, headers=_internal_headers())
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
    """Scheduled per-user scrape. Each approved user's criteria is scraped with
    their own Adzuna key; tracker-api attributes results to that user (review +
    AI-review enqueue), so the scraper only POSTs.
    """
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
        "scrape_all complete — %d users, %d raw seen, %d newly created",
        len(configs), total_seen, total_created,
    )


@app.task(name="app.tasks.scrape_user")
def scrape_user(user_id: str):
    """Scrape a single user's criteria immediately (e.g. after they edit criteria)."""
    configs = _fetch_user_configs() or []
    cfg = next((c for c in configs if str(c.get("user_id")) == str(user_id)), None)
    if not cfg:
        logger.warning("scrape_user: no active config for user %s", user_id)
        return
    seen, created = _scrape_for_config(cfg)
    logger.info("scrape_user %s complete — %d seen, %d created", user_id, seen, created)


def _scrape_for_config(cfg: dict) -> tuple[int, int]:
    """Scrape one user's criteria with their own Adzuna key. Returns (seen, created)."""
    user_id = cfg.get("user_id")
    keywords: list[str] = cfg.get("job_titles") or []
    if not keywords:
        return 0, 0
    locations = _ensure_remote_pass(cfg.get("search_locations") or ["Remote"])

    seen = created = 0
    for scraper in SCRAPERS:
        # BYOK sources are skipped entirely for users without a key.
        should_run, creds = _creds_for(scraper, cfg)
        if not should_run:
            continue
        for location in _locations_for(scraper, locations):
            try:
                raw_jobs = asyncio.run(scraper.scrape(keywords, location, creds))
            except Exception:
                logger.exception("scraper %s crashed for user %s", scraper.source_name, user_id)
                continue
            seen += len(raw_jobs)
            for raw in raw_jobs:
                if _post_job(raw, user_id=user_id):
                    created += 1

    # Company-board pass: once per user, not per location. Boards return ALL of
    # a company's roles; the scrapers prefilter titles against `keywords`.
    companies: list[str] = cfg.get("target_companies") or []
    if companies:
        for scraper in COMPANY_SCRAPERS:
            try:
                raw_jobs = asyncio.run(scraper.scrape_companies(companies, keywords))
            except Exception:
                logger.exception("company scraper %s crashed for user %s",
                                 scraper.source_name, user_id)
                continue
            seen += len(raw_jobs)
            for raw in raw_jobs:
                if _post_job(raw, user_id=user_id):
                    created += 1
    return seen, created


# ── Helpers ───────────────────────────────────────────────────

_REMOTE_ALIASES = {"remote", "anywhere"}

# JSearch's free tier is a hard monthly quota; cap the location fan-out.
_JSEARCH_MAX_LOCATIONS = 3


def _creds_for(scraper: BaseScraper, cfg: dict) -> tuple[bool, Creds]:
    """(should_run, creds) for one scraper given a user's config.

    BYOK sources return should_run=False when the user has no key; public
    sources always run with creds=None.
    """
    if scraper.source_name == "adzuna":
        creds = cfg.get("adzuna")  # {app_id, app_key} or None
        return bool(creds), creds
    if scraper.source_name == "jsearch":
        key = cfg.get("jsearch_api_key")
        if not key:
            return False, None
        # user_id rides along for the per-user 24h throttle key.
        return True, {"api_key": key, "user_id": str(cfg.get("user_id"))}
    return True, None


def _locations_for(scraper: BaseScraper, locations: list[str]) -> list[str]:
    """Quota-bound sources get a capped location list; others get all of them."""
    if scraper.source_name == "jsearch":
        return locations[:_JSEARCH_MAX_LOCATIONS]
    return locations


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


def _fetch_user_configs() -> list[dict] | None:
    """Return per-user scrape configs (criteria + decrypted Adzuna creds)."""
    url = f"{settings.tracker_api_url}/criteria/scraper/user-configs"
    try:
        resp = httpx.get(url, timeout=10, headers=_internal_headers())
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("Failed to fetch user configs from %s", url)
        return None


def _post_job(raw: RawJob, user_id: str) -> str | None:
    """
    POST a RawJob to tracker-api as a JobCreate payload, attributed to `user_id`
    (per-user BYOK). tracker-api dedups the shared Job and creates this user's
    review.

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
    params = {"user_id": user_id}
    try:
        resp = httpx.post(url, json=payload, params=params, timeout=15,
                          headers=_internal_headers())
        resp.raise_for_status()
        if resp.status_code == 201:
            return resp.json()["id"]
        return None  # 200 = job already existed
    except Exception:
        logger.exception("Failed to post job '%s' (%s)", raw.title, raw.external_id)
        return None
