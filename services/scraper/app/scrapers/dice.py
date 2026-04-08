"""Dice / Stack Overflow Jobs scraper — Phase 2"""

from typing import List
from app.scrapers.base import BaseScraper, RawJob


class DiceScraper(BaseScraper):
    source_name = "dice"

    async def scrape(self, keywords: List[str], location: str) -> List[RawJob]:
        raise NotImplementedError("Dice scraper coming in Phase 2")
