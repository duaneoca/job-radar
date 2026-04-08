"""
Base scraper interface. All scrapers implement this contract.
Phase 2: flesh out each concrete scraper.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RawJob:
    """Raw job data before normalization and storage."""
    external_id: str
    source: str  # indeed | linkedin | glassdoor | dice
    title: str
    company: str
    location: Optional[str]
    remote: bool
    salary_raw: Optional[str]
    description: str
    url: str
    date_posted: Optional[str]


class BaseScraper(ABC):
    source_name: str

    @abstractmethod
    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        """Scrape jobs and return a list of RawJob objects."""
        ...
