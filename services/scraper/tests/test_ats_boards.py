"""Per-ATS _to_raw_job mapping + the mandatory title prefilter.

Fixture dicts mirror the live payload shapes verified 2026-07-11:
Greenhouse boards-api (entity-escaped content), Ashby posting-api
(scrapeableCompensationSalarySummary), Lever v0 postings (epoch-ms createdAt).
"""

import asyncio
from unittest.mock import AsyncMock, patch

from app.scrapers.ats_boards import (
    AshbyScraper, GreenhouseScraper, LeverScraper, _parse_salary_text,
)

GH_ITEM = {
    "id": 7954688,
    "title": "Forward Deployed Engineer",
    "location": {"name": "San Francisco, CA"},
    "absolute_url": "https://stripe.com/jobs/search?gh_jid=7954688",
    "content": "&lt;h2&gt;Who we are&lt;/h2&gt;&lt;p&gt;Pay: $180,000 - $220,000 a year&lt;/p&gt;",
    "updated_at": "2026-06-26T17:05:44-04:00",
    "first_published": "2026-06-02T08:58:57-04:00",
}

ASHBY_ITEM = {
    "id": "03e2d4e1-73ad-4f09-a058-2eb9ce34c2bc",
    "title": "Solutions Architect",
    "location": "Remote (US)",
    "secondaryLocations": [{"location": "San Francisco, CA"}],
    "isRemote": True,
    "isListed": True,
    "jobUrl": "https://jobs.ashbyhq.com/ramp/03e2d4e1-73ad-4f09-a058-2eb9ce34c2bc",
    "applyUrl": "https://jobs.ashbyhq.com/ramp/03e2d4e1-73ad-4f09-a058-2eb9ce34c2bc/application",
    "descriptionPlain": "ABOUT RAMP ...",
    "publishedAt": "2026-07-07T20:47:09.753+00:00",
    "compensation": {"scrapeableCompensationSalarySummary": "$151K - $231K"},
}

LEVER_ITEM = {
    "id": "66acb66f-de37-4d95-a353-874db92838ef",
    "text": "Forward Deployed Engineer",
    "categories": {"location": "London", "allLocations": ["London", "Remote"]},
    "workplaceType": "remote",
    "hostedUrl": "https://jobs.lever.co/spotify/66acb66f",
    "descriptionPlain": "Support what you love...",
    "createdAt": 1781109739214,
    "salaryRange": {"min": 150000, "max": 200000, "currency": "USD", "interval": "per-year-salary"},
}


def test_greenhouse_mapping():
    raw = GreenhouseScraper()._to_raw_job(GH_ITEM, "Stripe")
    assert raw.external_id == "7954688"          # raw numeric — bookmarklet convention
    assert raw.source == "greenhouse"
    assert raw.company == "Stripe"               # user's entry as typed
    assert raw.location == "San Francisco, CA"
    assert "<" not in raw.description and "Who we are" in raw.description  # unescaped + stripped
    assert (raw.salary_min, raw.salary_max) == (180000, 220000)
    assert raw.date_posted == "2026-06-02T08:58:57-04:00"  # first_published preferred


def test_ashby_mapping():
    raw = AshbyScraper()._to_raw_job(ASHBY_ITEM, "Ramp")
    assert raw.external_id == "03e2d4e1-73ad-4f09-a058-2eb9ce34c2bc"  # raw uuid
    assert raw.url.endswith("/03e2d4e1-73ad-4f09-a058-2eb9ce34c2bc")  # jobUrl, not applyUrl
    assert raw.remote is True
    assert "San Francisco" in raw.location
    assert (raw.salary_min, raw.salary_max) == (151000, 231000)


def test_ashby_unlisted_dropped():
    assert AshbyScraper()._to_raw_job({**ASHBY_ITEM, "isListed": False}, "Ramp") is None


def test_lever_mapping():
    raw = LeverScraper()._to_raw_job(LEVER_ITEM, "Spotify")
    assert raw.external_id == "66acb66f-de37-4d95-a353-874db92838ef"
    assert raw.remote is True                     # workplaceType
    assert raw.location == "London, Remote"       # allLocations joined
    assert (raw.salary_min, raw.salary_max) == (150000, 200000)
    assert raw.date_posted.startswith("2026-")    # epoch ms → ISO


def test_lever_non_usd_salary_dropped():
    item = {**LEVER_ITEM, "salaryRange": {"min": 1, "max": 2, "currency": "EUR", "interval": "per-year-salary"}}
    raw = LeverScraper()._to_raw_job(item, "Spotify")
    assert raw.salary_min is None and raw.salary_max is None


def test_salary_text_parsing():
    assert _parse_salary_text("$151K - $231K") == (151000, 231000)
    assert _parse_salary_text("$180,000 – $220,000") == (180000, 220000)
    assert _parse_salary_text("competitive") == (None, None)


def test_title_prefilter_drops_nonmatching(monkeypatch):
    """scrape_companies keeps only postings whose title matches the keywords."""
    scraper = GreenhouseScraper()
    items = [
        GH_ITEM,                                            # "Forward Deployed Engineer" — match
        {**GH_ITEM, "id": 2, "title": "Account Executive"}, # no match
    ]

    async def fake_fetch(client, company):
        return items

    monkeypatch.setattr(scraper, "_fetch_company", fake_fetch)
    jobs = asyncio.run(scraper.scrape_companies(["Stripe"], ["Forward Deployed Engineer"]))
    assert [j.external_id for j in jobs] == ["7954688"]


def test_no_keywords_refuses_to_scrape():
    scraper = GreenhouseScraper()
    jobs = asyncio.run(scraper.scrape_companies(["Stripe"], []))
    assert jobs == []


def test_429_probe_not_cached_as_miss():
    """A rate-limited probe must stay unknown (retried next run), not become a miss."""
    scraper = GreenhouseScraper()

    class Resp429:
        status_code = 429
        headers = {"Retry-After": "60"}

    async def run():
        with patch.object(scraper, "_get", new=AsyncMock(return_value=None)) as _g:
            # _get returns None for 429/transport per implementation
            async with _fake_client() as client:
                return await scraper._fetch_company(client, "Stripe")

    result = asyncio.run(run())
    assert result is None
    assert scraper._cache.get("greenhouse", "Stripe") is None  # unknown, NOT cached miss


class _fake_client:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
