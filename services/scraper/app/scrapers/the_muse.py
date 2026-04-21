"""
The Muse API client.

Docs: https://www.themuse.com/developers/api/v2
Public API, no auth required.

The Muse does not support free-form keyword search — it filters by
`category` (broad vertical like "Software Engineering"), `level`, and
`location`. We map our criteria's keywords onto the Software Engineering
category and return everything; downstream stage-1 rules + stage-2 AI
review do the real filtering.
"""

import logging
import re
from typing import List, Optional

import httpx

from app.scrapers.base import BaseScraper, RawJob

logger = logging.getLogger(__name__)

_BASE = "https://www.themuse.com/api/public/jobs"
_MAX_PAGES = 3           # ~60 jobs per category per run
_TIMEOUT = 20.0

# The Muse returns very broad categories. We pull the ones that could
# contain software engineering roles; duplicates across categories are
# deduped by external_id at the top of `scrape()`.
_CATEGORIES = [
    "Software Engineering",
    "Data Science",
    "Data and Analytics",
]


class TheMuseScraper(BaseScraper):
    source_name = "the_muse"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        all_jobs: List[RawJob] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for category in _CATEGORIES:
                logger.info("The Muse: category '%s'", category)
                try:
                    jobs = await self._scrape_category(client, category, location)
                    logger.info("The Muse '%s' → %d jobs", category, len(jobs))
                    all_jobs.extend(jobs)
                except Exception:
                    logger.exception("The Muse category '%s' failed", category)

        seen: set = set()
        unique: List[RawJob] = []
        for job in all_jobs:
            if job.external_id not in seen:
                seen.add(job.external_id)
                unique.append(job)

        logger.info("The Muse total unique: %d", len(unique))
        return unique

    async def _scrape_category(
        self, client: httpx.AsyncClient, category: str, location: str
    ) -> List[RawJob]:
        jobs: List[RawJob] = []

        for page in range(_MAX_PAGES):
            params = {
                "page": page,
                "category": category,
                "descending": "true",
            }
            # The Muse's location filter is a free-text string; only set it
            # when we have a concrete city, and request remote jobs by
            # passing the literal "Flexible / Remote" value they use.
            if location and location.lower() in ("remote", "anywhere"):
                params["location"] = "Flexible / Remote"
            elif location:
                params["location"] = location

            resp = await client.get(_BASE, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "The Muse returned %d: %s", resp.status_code, resp.text[:200]
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

            # Respect the API's page count if present
            page_count = data.get("page_count")
            if page_count is not None and page >= page_count - 1:
                break

        return jobs


def _to_raw_job(item: dict) -> Optional[RawJob]:
    try:
        ext_id = str(item["id"])
    except KeyError:
        return None

    company_obj = item.get("company") or {}
    company_name = company_obj.get("name") or "Unknown Company"

    locations = item.get("locations") or []
    location_text: Optional[str] = None
    if locations:
        location_text = ", ".join(loc.get("name", "") for loc in locations if loc.get("name"))
    is_remote = bool(location_text and "remote" in location_text.lower())

    refs = item.get("refs") or {}
    url = refs.get("landing_page") or ""

    # `contents` is HTML; strip it for a plain-text description.
    description = _strip_html(item.get("contents") or "")

    return RawJob(
        external_id=f"muse_{ext_id}",
        source="the_muse",
        title=item.get("name") or "Unknown Title",
        company=company_name,
        location=location_text or None,
        remote=is_remote,
        description=description,
        url=url,
        date_posted=item.get("publication_date"),
    )


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """Cheap HTML → plain text. Good enough for AI review input."""
    if not text:
        return ""
    without_tags = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", without_tags).strip()
