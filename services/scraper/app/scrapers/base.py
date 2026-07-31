"""
Base scraper interface. Each concrete scraper is a thin API client for one
public jobs API that returns a normalized list of `RawJob` objects.

Phase 2 uses only aggregator APIs (no HTML scraping) because datacenter IPs
get blocked on the first request by Cloudflare-protected boards like Indeed
and LinkedIn. See `memory/project_scraping_strategy.md` for the rationale.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

# Per-user Adzuna credentials, or None. Public sources ignore this.
Creds = Optional[dict]


@dataclass
class RawJob:
    """Normalized job data before it's POSTed to tracker-api."""
    external_id: str
    source: str           # adzuna | the_muse | remotive | ...
    title: str
    company: str
    location: Optional[str]
    remote: bool
    description: str
    url: str
    salary_min: Optional[int] = None   # annualized USD
    salary_max: Optional[int] = None   # annualized USD
    date_posted: Optional[str] = None  # ISO-8601 string or None


class BaseScraper(ABC):
    source_name: str

    @abstractmethod
    async def scrape(self, keywords: List[str], location: str, creds: Creds = None) -> List[RawJob]:
        """Scrape jobs for the given keywords + location and return raw results.

        `creds` carries per-user Adzuna credentials in BYOK mode; public sources
        ignore it.
        """
        ...


@dataclass
class BoardScrape:
    """The result of one company-board pass.

    `live_ids` is the absence signal, and it is deliberately narrow: a company
    appears ONLY if its board was actually read (HTTP 200 with a valid payload).
    A 429, a timeout, or a slug that stopped resolving must never look like "the
    board is empty" — that would expire every job at that company in one sweep.

    The ids are collected BEFORE the title prefilter, so editing your job titles
    can never expire postings you already collected. Absence means the employer
    took the listing down, nothing else.
    """
    jobs: List[RawJob]
    live_ids: dict          # company -> set of every external_id on its board


class CompanyBoardScraper(ABC):
    """Watches specific companies' public ATS boards (Greenhouse/Lever/Ashby).

    Company-based rather than search-based: boards return ALL of a company's
    open roles, so implementations MUST prefilter titles against `keywords`
    before emitting — otherwise a handful of watched companies floods stage-2
    AI scoring. Runs once per user (locations don't apply to a board).

    Returning ALL open roles is also what makes absence meaningful here, unlike
    an aggregator search which returns a ranked, truncated slice.
    """
    source_name: str

    @abstractmethod
    async def scrape_companies(self, companies: List[str], keywords: List[str]) -> BoardScrape:
        ...
