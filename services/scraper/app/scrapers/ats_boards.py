"""Public ATS board watchers — Greenhouse, Ashby, Lever.

Free, keyless JSON endpoints that return every open role for one company:

  Greenhouse  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  Ashby       https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
  Lever       https://api.lever.co/v0/postings/{slug}?mode=json

Driven by the user's `target_companies` criterion (see CompanyBoardScraper).
Slugs are auto-probed from the company name and cached (slugs.py) — the probe
IS the fetch, so a resolved slug costs no extra request.

Politeness (these endpoints publish no hard limits, but the existing sources
set no protections at all — these do better):
  - shared identifying User-Agent
  - sequential fetches with a small delay between companies
  - 429/Retry-After treated as transient: skip this run, never cache as a miss
  - per-company crash isolation

external_id convention: the board's RAW id (Greenhouse numeric id, Ashby/Lever
UUID) with NO prefix — identical to what the bookmarklet extracts, so scraped
jobs dedupe against bookmarklet imports of the same posting.
"""

import asyncio
import html as _html
import logging
import re
from abc import abstractmethod
from typing import List, Optional

import httpx

from app.scrapers.base import CompanyBoardScraper, RawJob
from app.scrapers.filtering import keyword_tokens, strip_html, title_matches
from app.scrapers.slugs import SlugCache, candidate_slugs

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0
_DELAY_BETWEEN_COMPANIES = 0.5  # seconds
_USER_AGENT = "JobRadar/1.0 (+https://job-radar.net)"

# Same K-aware salary pattern the bookmarklet uses ($151K - $231K, $120,000–$150,000).
_SALARY_RE = re.compile(
    r"\$([\d,]+(?:\.\d+)?)(K?)\s*[-–—]\s*\$([\d,]+(?:\.\d+)?)(K?)", re.IGNORECASE
)


def _parse_salary_text(text: str) -> tuple[Optional[int], Optional[int]]:
    m = _SALARY_RE.search(text or "")
    if not m:
        return None, None
    def _num(val: str, k: str) -> int:
        return round(float(val.replace(",", "")) * (1000 if k.upper() == "K" else 1))
    return _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))


class _ATSBase(CompanyBoardScraper):
    """Shared probe/fetch/filter plumbing; subclasses supply URL + mapping."""

    def __init__(self, cache: SlugCache | None = None):
        self._cache = cache or SlugCache()

    # ── per-ATS hooks ────────────────────────────────────────

    @abstractmethod
    def _board_url(self, slug: str) -> str: ...

    @abstractmethod
    def _items(self, payload) -> Optional[list]:
        """Extract the postings list from a 200 response body (None = malformed)."""
        ...

    @abstractmethod
    def _to_raw_job(self, item: dict, company: str) -> Optional[RawJob]: ...

    # ── shared loop ──────────────────────────────────────────

    async def scrape_companies(self, companies: List[str], keywords: List[str]) -> List[RawJob]:
        tokens = keyword_tokens(keywords)
        if not tokens:
            return []  # nothing to prefilter against — refuse to flood scoring

        jobs: List[RawJob] = []
        seen: set[str] = set()
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            for i, company in enumerate(companies):
                if i:
                    await asyncio.sleep(_DELAY_BETWEEN_COMPANIES)
                try:
                    items = await self._fetch_company(client, company)
                except Exception:
                    logger.exception("%s board fetch crashed for '%s'", self.source_name, company)
                    continue
                if items is None:
                    continue
                kept = 0
                for item in items:
                    raw = self._to_raw_job(item, company)
                    if raw is None or raw.external_id in seen:
                        continue
                    if not title_matches(raw.title, tokens):
                        continue
                    seen.add(raw.external_id)
                    jobs.append(raw)
                    kept += 1
                logger.info("%s/%s: %d postings, %d after title filter",
                            self.source_name, company, len(items), kept)
        return jobs

    async def _fetch_company(self, client: httpx.AsyncClient, company: str) -> Optional[list]:
        """Resolve the slug (cached or by probing) and return its postings.

        The probe doubles as the fetch: the first 200 response resolves the
        slug AND supplies this run's payload.
        """
        cached = self._cache.get(self.source_name, company)
        if cached == "":  # cached miss
            return None
        slugs = [cached] if cached else candidate_slugs(company)

        for slug in slugs:
            resp = await self._get(client, slug)
            if resp is None:          # transient (429/transport) — don't cache anything
                return None
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                logger.warning("%s/%s returned %d", self.source_name, slug, resp.status_code)
                return None
            try:
                items = self._items(resp.json())
            except ValueError:
                items = None
            if items is None:         # 200 but not a board payload — treat as no-board
                continue
            self._cache.set(self.source_name, company, slug)
            return items

        if not cached:  # exhausted candidates — cache the miss (24h)
            logger.info("No %s board found for '%s' (tried %s)",
                        self.source_name, company, ", ".join(slugs))
            self._cache.set(self.source_name, company, None)
        return None

    async def _get(self, client: httpx.AsyncClient, slug: str) -> Optional[httpx.Response]:
        try:
            resp = await client.get(self._board_url(slug))
        except Exception:
            logger.warning("%s/%s request failed (transport)", self.source_name, slug)
            return None
        if resp.status_code == 429:
            logger.warning("%s rate-limited (429, Retry-After=%s) — skipping this run",
                           self.source_name, resp.headers.get("Retry-After"))
            return None
        return resp


class GreenhouseScraper(_ATSBase):
    source_name = "greenhouse"

    def _board_url(self, slug: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def _items(self, payload) -> Optional[list]:
        if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
            return payload["jobs"]
        return None

    def _to_raw_job(self, item: dict, company: str) -> Optional[RawJob]:
        ext_id = item.get("id")
        title = item.get("title")
        url = item.get("absolute_url")
        if not (ext_id and title and url):
            return None
        # Greenhouse entity-escapes the HTML content (&lt;p&gt;…) — unescape first.
        description = strip_html(_html.unescape(item.get("content") or ""))
        location = (item.get("location") or {}).get("name")
        sal_min, sal_max = _parse_salary_text(description)
        return RawJob(
            external_id=str(ext_id),  # raw numeric id — matches the bookmarklet
            source="greenhouse",
            title=title,
            company=company,
            location=location,
            remote="remote" in (location or "").lower(),
            description=description,
            url=url,
            salary_min=sal_min,
            salary_max=sal_max,
            date_posted=item.get("first_published") or item.get("updated_at"),
        )


class AshbyScraper(_ATSBase):
    source_name = "ashby"

    def _board_url(self, slug: str) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

    def _items(self, payload) -> Optional[list]:
        if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
            return payload["jobs"]
        return None

    def _to_raw_job(self, item: dict, company: str) -> Optional[RawJob]:
        if item.get("isListed") is False:
            return None
        ext_id = item.get("id")
        title = item.get("title")
        url = item.get("jobUrl") or item.get("applyUrl")
        if not (ext_id and title and url):
            return None
        location = item.get("location")
        secondary = [s.get("location") for s in (item.get("secondaryLocations") or [])]
        if secondary:
            location = ", ".join(filter(None, [location, *secondary]))
        comp = item.get("compensation") or {}
        sal_min, sal_max = _parse_salary_text(
            comp.get("scrapeableCompensationSalarySummary")
            or comp.get("compensationTierSummary") or ""
        )
        return RawJob(
            external_id=str(ext_id),  # raw UUID — matches the bookmarklet
            source="ashby",
            title=title,
            company=company,
            location=location,
            remote=bool(item.get("isRemote")),
            description=item.get("descriptionPlain") or strip_html(item.get("descriptionHtml") or ""),
            url=url,
            salary_min=sal_min,
            salary_max=sal_max,
            date_posted=item.get("publishedAt"),
        )


class LeverScraper(_ATSBase):
    source_name = "lever"

    def _board_url(self, slug: str) -> str:
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"

    def _items(self, payload) -> Optional[list]:
        # Lever returns a bare list; error bodies are dicts ({"ok": false, ...}).
        return payload if isinstance(payload, list) else None

    def _to_raw_job(self, item: dict, company: str) -> Optional[RawJob]:
        ext_id = item.get("id")
        title = item.get("text")
        url = item.get("hostedUrl")
        if not (ext_id and title and url):
            return None
        cats = item.get("categories") or {}
        all_locations = cats.get("allLocations") or []
        location = ", ".join(all_locations) if all_locations else cats.get("location")
        workplace = (item.get("workplaceType") or "").lower()
        sal_min = sal_max = None
        rng = item.get("salaryRange") or {}
        if (rng.get("currency") or "USD") == "USD" and (rng.get("interval") or "").startswith("per-year"):
            sal_min = int(rng["min"]) if rng.get("min") else None
            sal_max = int(rng["max"]) if rng.get("max") else None
        created = item.get("createdAt")  # epoch ms
        date_posted = None
        if isinstance(created, (int, float)):
            from datetime import datetime, timezone
            date_posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
        return RawJob(
            external_id=str(ext_id),  # raw UUID (sets the convention for Lever)
            source="lever",
            title=title,
            company=company,
            location=location,
            remote=workplace == "remote" or "remote" in (location or "").lower(),
            description=item.get("descriptionPlain") or strip_html(item.get("description") or ""),
            url=url,
            salary_min=sal_min,
            salary_max=sal_max,
            date_posted=date_posted,
        )
