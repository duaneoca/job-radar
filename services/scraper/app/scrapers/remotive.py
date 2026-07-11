"""
Remotive API client.

Docs: https://github.com/remotive-com/remote-jobs-api
Public API, no auth required. Remote-only jobs, heavy on tech.

Remotive exposes a single endpoint that returns all active postings; we
filter client-side by keyword. There is no pagination — one request
returns the full list (typically 1–2k entries), which is cheap and easy.
"""

import logging
from typing import List, Optional

import httpx

from app.scrapers.base import BaseScraper, Creds, RawJob
from app.scrapers.filtering import keyword_tokens as _keyword_tokens
from app.scrapers.filtering import matches_any_token as _matches_any_token
from app.scrapers.filtering import strip_html as _strip_html

logger = logging.getLogger(__name__)

_ENDPOINT = "https://remotive.com/api/remote-jobs"
_TIMEOUT = 30.0


class RemotiveScraper(BaseScraper):
    source_name = "remotive"

    async def scrape(self, keywords: List[str], location: str, creds: Creds = None) -> List[RawJob]:
        # Remotive is remote-only; if the caller asked for a concrete
        # non-remote location, we just return nothing (still cheaper than
        # not running at all because the "Remote" pass will hit this).
        if location and location.lower() not in ("remote", "anywhere", ""):
            logger.info("Remotive skipped for non-remote location '%s'", location)
            return []

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                resp = await client.get(
                    _ENDPOINT,
                    params={"category": "software-dev"},
                )
            except Exception:
                logger.exception("Remotive request failed")
                return []

            if resp.status_code != 200:
                logger.warning(
                    "Remotive returned %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return []

            data = resp.json()

        all_items = data.get("jobs") or []
        logger.info("Remotive pulled %d total remote software-dev jobs", len(all_items))

        # Build a set of lower-cased tokens from every keyword. The criteria
        # field gives us multi-word phrases like "Senior Software Engineer";
        # we want to keep any job whose title or tags contain ANY of those
        # tokens. Stage-1 filters are intentionally broad — stage-2 AI
        # review does the real selection.
        tokens = _keyword_tokens(keywords)

        jobs: List[RawJob] = []
        for item in all_items:
            if tokens and not _matches_any_token(item, tokens):
                continue
            raw = _to_raw_job(item)
            if raw is not None:
                jobs.append(raw)

        logger.info("Remotive after keyword filter: %d", len(jobs))
        return jobs


def _to_raw_job(item: dict) -> Optional[RawJob]:
    try:
        ext_id = str(item["id"])
    except KeyError:
        return None

    location_text = item.get("candidate_required_location") or "Worldwide"

    return RawJob(
        external_id=f"remotive_{ext_id}",
        source="remotive",
        title=item.get("title") or "Unknown Title",
        company=item.get("company_name") or "Unknown Company",
        location=location_text,
        remote=True,  # Remotive is remote-only by definition
        description=_strip_html(item.get("description") or ""),
        url=item.get("url") or "",
        date_posted=item.get("publication_date"),
    )
