"""
Indeed scraper — Phase 2
Uses Playwright for JS rendering.
"""

from typing import List
from app.scrapers.base import BaseScraper, RawJob


class IndeedScraper(BaseScraper):
    source_name = "indeed"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        # TODO (Phase 2): implement with Playwright
        raise NotImplementedError("Indeed scraper coming in Phase 2")
