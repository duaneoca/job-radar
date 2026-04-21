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
    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        """Scrape jobs for the given keywords + location and return raw results."""
        ...
