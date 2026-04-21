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

from app.scrapers.base import BaseScraper, RawJob

logger = logging.getLogger(__name__)

_ENDPOINT = "https://remotive.com/api/remote-jobs"
_TIMEOUT = 30.0


class RemotiveScraper(BaseScraper):
    source_name = "remotive"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
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


# Words that are too generic to be useful as a standalone match.
_STOPWORDS = {"senior", "junior", "staff", "lead", "principal", "engineer", "developer"}


def _keyword_tokens(keywords: List[str]) -> set[str]:
    tokens: set[str] = set()
    for kw in keywords or []:
        for tok in kw.lower().split():
            if len(tok) >= 3 and tok not in _STOPWORDS:
                tokens.add(tok)
    return tokens


def _matches_any_token(item: dict, tokens: set[str]) -> bool:
    title = (item.get("title") or "").lower()
    tags = [t.lower() for t in (item.get("tags") or [])]
    for tok in tokens:
        if tok in title:
            return True
        for tag in tags:
            if tok in tag:
                return True
    return False


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


# Keep import footprint small — reuse the logic locally instead of
# pulling in a shared util module for one regex.
import re as _re
_HTML_TAG_RE = _re.compile(r"<[^>]+>")
_WHITESPACE_RE = _re.compile(r"\s+")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", text)).strip()
