"""
LinkedIn scraper — Phase 2
LinkedIn uses aggressive bot detection; Playwright + stealth plugin required.
"""

from typing import List
from app.scrapers.base import BaseScraper, RawJob


class LinkedInScraper(BaseScraper):
    source_name = "linkedin"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        # TODO (Phase 2): implement with Playwright + stealth
        raise NotImplementedError("LinkedIn scraper coming in Phase 2")
