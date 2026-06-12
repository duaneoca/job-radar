"""
The Muse API client.

Docs: https://www.themuse.com/developers/api/v2
Public API, no auth required.

The Muse does not support free-form keyword search — it filters by
`category` (broad vertical like "Software Engineering"), `level`, and
`location`. We map the user's job titles onto the relevant Muse categories and
request only those; downstream stage-1 rules + stage-2 AI review do the real
filtering. If nothing maps, we return no Muse results (rather than defaulting to
software/data) so a non-tech searcher doesn't get irrelevant jobs.
"""

import logging
import re
from typing import List, Optional

import httpx

from app.scrapers.base import BaseScraper, Creds, RawJob

logger = logging.getLogger(__name__)

_BASE = "https://www.themuse.com/api/public/jobs"
_MAX_PAGES = 3           # ~60 jobs per category per run
_TIMEOUT = 20.0
_MAX_CATEGORIES = 5      # cap API fan-out per run

# Best-effort map of job-title trigger substrings → a Muse category. A user's
# keywords are matched against these; matched categories are requested. Wrong or
# unknown categories degrade gracefully (the API returns nothing, we skip).
_CATEGORY_TRIGGERS: List[tuple] = [
    (("software", "developer", "full stack", "fullstack", "backend", "back end",
      "frontend", "front end", "devops", "sre", "architect", "engineer"), "Software Engineering"),
    (("data scien", "machine learning", "ml engineer", "ai engineer", "deep learning"), "Data Science"),
    (("data analyst", "analytics", "business intelligence", "bi analyst"), "Data and Analytics"),
    (("designer", "ux", "ui/ux", "product design", "graphic design"), "Design and UX"),
    (("product manager", "product management", "product owner"), "Product Management"),
    (("project manager", "program manager", "scrum master", "delivery manager"), "Project Management"),
    (("sales", "account executive", "business development", "sdr"), "Sales"),
    (("marketing", "growth marketing", "seo", "demand gen"), "Marketing"),
    (("accountant", "accounting", "finance", "financial", "fp&a", "controller"), "Accounting and Finance"),
    (("recruiter", "recruiting", "human resources", "people ops", "talent acquisition"), "Human Resources"),
    (("customer success", "customer service", "support specialist", "customer support"), "Customer Service"),
    (("operations", "supply chain", "logistics"), "Operations"),
    (("legal", "counsel", "attorney", "paralegal"), "Legal"),
    (("writer", "editor", "content", "copywriter"), "Writing and Editing"),
]


def _categories_for_keywords(keywords: List[str]) -> List[str]:
    """Map a user's job titles to the Muse categories to request. Empty list
    means "no Muse pass" (no software/data default for non-tech searchers)."""
    joined = " ".join(keywords).lower()
    cats: List[str] = []
    for triggers, category in _CATEGORY_TRIGGERS:
        if category in cats:
            continue
        if any(t in joined for t in triggers):
            cats.append(category)
    return cats[:_MAX_CATEGORIES]


class TheMuseScraper(BaseScraper):
    source_name = "the_muse"

    async def scrape(self, keywords: List[str], location: str, creds: Creds = None) -> List[RawJob]:
        categories = _categories_for_keywords(keywords)
        if not categories:
            logger.info("The Muse: no category matched keywords %s — skipping", keywords)
            return []

        all_jobs: List[RawJob] = []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for category in categories:
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
