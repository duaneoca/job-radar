"""
JSearch API client (RapidAPI) — BYOK per user.

Docs: https://www.openwebninja.com/api/jsearch (hosted on RapidAPI as
jsearch.p.rapidapi.com). Searches the Google-for-Jobs index, which surfaces
LinkedIn/Indeed/Glassdoor postings that direct scraping can't reach.

The free tier is a HARD 200 requests/month, so this scraper is aggressively
budgeted:
  - ONE request per location: all job titles OR-combined into a single query
  - locations are capped upstream (main.py) at 3 per user
  - num_pages=1, date_posted=week
  - a redis-backed 24h per-user throttle skips runs entirely between windows
    (the 6h Beat schedule would otherwise burn 4x the budget)
  ⇒ worst case ≈ 3 requests/day/user ≈ 90/month, inside the free tier.
429 or quota exhaustion is logged and skipped — never crashes the scrape.
"""

import logging
import time
from typing import List, Optional

import httpx
import redis

from app.config import settings
from app.scrapers.base import BaseScraper, Creds, RawJob

logger = logging.getLogger(__name__)

_BASE = "https://jsearch.p.rapidapi.com/search"
_HOST = "jsearch.p.rapidapi.com"
_TIMEOUT = 20.0
_MAX_TOTAL = 60           # per call — one page is ~10, this is a safety cap
_THROTTLE_TTL = 24 * 3600
# One scrape run calls scrape() once per location back-to-back. The first call
# claims the 24h window; calls within this grace period belong to the same run.
_RUN_GRACE = 900

_REMOTE_ALIASES = {"remote", "anywhere", ""}


class RunThrottle:
    """At most one JSearch pass per user per 24h (redis, local-dict fallback)."""

    def __init__(self, url: str | None = None):
        self._local: dict[str, float] = {}
        try:
            self._redis = redis.Redis.from_url(url or settings.redis_url,
                                               socket_timeout=2, decode_responses=True)
        except Exception:  # pragma: no cover
            self._redis = None

    @staticmethod
    def _key(user_id: str) -> str:
        return f"jsearch:last:{user_id}"

    def allow(self, user_id: str) -> bool:
        key = self._key(user_id)
        now = time.time()
        stamp = self._read(key)
        if stamp is not None:
            # Same run (other locations) → allowed; earlier run → throttled.
            return (now - stamp) < _RUN_GRACE
        self._write(key, now)
        return True

    def _read(self, key: str) -> float | None:
        if self._redis is not None:
            try:
                val = self._redis.get(key)
                return float(val) if val is not None else None
            except Exception:
                logger.warning("jsearch throttle read failed (redis); using local fallback")
        stamp = self._local.get(key)
        return stamp if stamp is not None and (time.time() - stamp) < _THROTTLE_TTL else None

    def _write(self, key: str, stamp: float) -> None:
        self._local[key] = stamp
        if self._redis is not None:
            try:
                self._redis.setex(key, _THROTTLE_TTL, str(stamp))
            except Exception:
                logger.warning("jsearch throttle write failed (redis); kept local only")


class JSearchScraper(BaseScraper):
    source_name = "jsearch"

    def __init__(self, throttle: RunThrottle | None = None):
        self._throttle = throttle if throttle is not None else RunThrottle()

    async def scrape(self, keywords: List[str], location: str, creds: Creds = None) -> List[RawJob]:
        api_key = (creds or {}).get("api_key")
        user_id = (creds or {}).get("user_id")
        if not api_key or not user_id:
            logger.warning("JSearch called without credentials — skipping")
            return []
        if not keywords:
            return []
        if not self._throttle.allow(user_id):
            logger.info("JSearch throttled for user %s (ran within 24h) — skipping", user_id)
            return []

        query, params = _build_query(keywords, location)
        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": _HOST,
            "User-Agent": "JobRadar/1.0 (+https://job-radar.net)",
        }
        logger.info("JSearch: '%s'", query)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_BASE, params=params, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("JSearch request failed: %s", exc)
            return []

        if resp.status_code == 429:
            logger.warning("JSearch rate-limited (Retry-After: %s) — skipping",
                           resp.headers.get("Retry-After"))
            return []
        if resp.status_code in (401, 403):
            logger.warning("JSearch auth/quota rejection (%d) for user %s — check the key",
                           resp.status_code, user_id)
            return []
        if resp.status_code != 200:
            logger.warning("JSearch returned %d: %s", resp.status_code, resp.text[:200])
            return []

        items = (resp.json() or {}).get("data") or []
        jobs: List[RawJob] = []
        seen: set = set()
        for item in items[:_MAX_TOTAL]:
            job = _to_raw_job(item)
            if job is not None and job.external_id not in seen:
                seen.add(job.external_id)
                jobs.append(job)
        logger.info("JSearch '%s' → %d jobs", query, len(jobs))
        return jobs


def _build_query(keywords: List[str], location: str) -> tuple[str, dict]:
    """One OR-combined query per location — the whole budget design."""
    query = " OR ".join(keywords)
    params = {
        "query": query,
        "page": 1,
        "num_pages": 1,
        "date_posted": "week",
        "country": "us",
    }
    if (location or "").strip().lower() in _REMOTE_ALIASES:
        params["work_from_home"] = "true"
    else:
        query = f"{query} in {location}"
        params["query"] = query
    return query, params


def _to_raw_job(item: dict) -> Optional[RawJob]:
    ext_id = item.get("job_id")
    if not ext_id:
        return None

    city, state = item.get("job_city"), item.get("job_state")
    location_text = item.get("job_location") or ", ".join(p for p in (city, state) if p) or None

    salary_min = salary_max = None
    if (item.get("job_salary_period") or "").upper() == "YEAR":
        salary_min = _to_int(item.get("job_min_salary"))
        salary_max = _to_int(item.get("job_max_salary"))

    return RawJob(
        external_id=str(ext_id),
        source="jsearch",
        title=item.get("job_title") or "Unknown Title",
        company=item.get("employer_name") or "Unknown Company",
        location=location_text,
        remote=bool(item.get("job_is_remote")),
        description=item.get("job_description") or "",
        url=item.get("job_apply_link") or "",
        salary_min=salary_min,
        salary_max=salary_max,
        date_posted=item.get("job_posted_at_datetime_utc"),
    )


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
