"""
Adzuna API client.

Docs: https://developer.adzuna.com/docs/search
Free tier: ~1000 calls/day, no credit card required.

Adzuna returns structured job data (title, company, location, salary_min,
salary_max, description, url) so we do no HTML parsing here.
"""

import logging
from typing import List, Optional

import httpx

from app.config import settings
from app.scrapers.base import BaseScraper, RawJob

logger = logging.getLogger(__name__)

_BASE = "https://api.adzuna.com/v1/api/jobs"
_COUNTRY = "us"
_RESULTS_PER_PAGE = 50   # Adzuna max
_MAX_PAGES = 2           # 100 jobs per keyword is plenty for stage-1 filter
_MAX_DAYS_OLD = 14
_TIMEOUT = 20.0


class AdzunaScraper(BaseScraper):
    source_name = "adzuna"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        if not (settings.adzuna_app_id and settings.adzuna_app_key):
            logger.warning("Adzuna credentials not set — skipping Adzuna")
            return []

        all_jobs: List[RawJob] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for keyword in keywords:
                logger.info("Adzuna: '%s' in '%s'", keyword, location)
                try:
                    jobs = await self._scrape_keyword(client, keyword, location)
                    logger.info("Adzuna '%s' → %d jobs", keyword, len(jobs))
                    all_jobs.extend(jobs)
                except Exception:
                    logger.exception("Adzuna keyword '%s' failed", keyword)

        # Dedupe by external_id in case the same posting matched multiple keywords
        seen: set = set()
        unique: List[RawJob] = []
        for job in all_jobs:
            if job.external_id not in seen:
                seen.add(job.external_id)
                unique.append(job)

        logger.info("Adzuna total unique: %d", len(unique))
        return unique

    async def _scrape_keyword(
        self, client: httpx.AsyncClient, keyword: str, location: str
    ) -> List[RawJob]:
        jobs: List[RawJob] = []

        for page in range(1, _MAX_PAGES + 1):
            url = f"{_BASE}/{_COUNTRY}/search/{page}"
            params = {
                "app_id": settings.adzuna_app_id,
                "app_key": settings.adzuna_app_key,
                "what": keyword,
                "results_per_page": _RESULTS_PER_PAGE,
                "max_days_old": _MAX_DAYS_OLD,
                "sort_by": "date",
            }
            # Adzuna treats "Remote" as a where filter poorly; only send it
            # when the user gave a real geographic location.
            if location and location.lower() not in ("remote", "anywhere", ""):
                params["where"] = location

            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "Adzuna %s returned %d: %s",
                    url, resp.status_code, resp.text[:200],
                )
                break

            data = resp.json()
            results = data.get("results") or []
            if not results:
                break

            for item in results:
                job = _to_raw_job(item)
                if job is not None:
                    jobs.append(job)

            if len(results) < _RESULTS_PER_PAGE:
                break  # fewer than full page → no more results

        return jobs


def _to_raw_job(item: dict) -> Optional[RawJob]:
    """Convert an Adzuna result dict into a RawJob."""
    try:
        ext_id = str(item["id"])
    except KeyError:
        return None

    location_obj = item.get("location") or {}
    location_text: Optional[str] = location_obj.get("display_name")
    is_remote = bool(location_text and "remote" in location_text.lower())

    company_obj = item.get("company") or {}
    company_name = company_obj.get("display_name") or "Unknown Company"

    salary_min = _to_int(item.get("salary_min"))
    salary_max = _to_int(item.get("salary_max"))

    return RawJob(
        external_id=ext_id,
        source="adzuna",
        title=item.get("title") or "Unknown Title",
        company=company_name,
        location=location_text,
        remote=is_remote,
        description=item.get("description") or "",
        url=item.get("redirect_url") or "",
        salary_min=salary_min,
        salary_max=salary_max,
        date_posted=item.get("created"),
    )


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
