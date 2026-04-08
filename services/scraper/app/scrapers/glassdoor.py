"""Glassdoor scraper — Phase 2"""

from typing import List
from app.scrapers.base import BaseScraper, RawJob


class GlassdoorScraper(BaseScraper):
    source_name = "glassdoor"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        raise NotImplementedError("Glassdoor scraper coming in Phase 2")
