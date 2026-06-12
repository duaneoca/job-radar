"""Tests for the per-user scrape dispatch (_scrape_for_config)."""

from unittest.mock import patch

from app import main


class FakeScraper:
    def __init__(self, name, jobs):
        self.source_name = name
        self._jobs = jobs
        self.last_creds = "UNSET"

    async def scrape(self, keywords, location, creds=None):
        self.last_creds = creds
        return list(self._jobs)


def test_adzuna_skipped_without_creds():
    adz = FakeScraper("adzuna", ["a"])
    muse = FakeScraper("the_muse", ["m"])
    cfg = {"user_id": "u1", "job_titles": ["eng"], "search_locations": ["Remote"], "adzuna": None}
    with patch.object(main, "SCRAPERS", [adz, muse]), \
         patch.object(main, "_post_job", return_value="id") as post:
        seen, _created = main._scrape_for_config(cfg)
    # Adzuna never ran (still UNSET); only Muse's one job was seen.
    assert adz.last_creds == "UNSET"
    assert seen == 1
    # Every posted job is attributed to the user.
    assert all(c.kwargs.get("user_id") == "u1" for c in post.call_args_list)


def test_adzuna_receives_user_creds():
    adz = FakeScraper("adzuna", ["a"])
    cfg = {
        "user_id": "u1", "job_titles": ["eng"], "search_locations": ["Remote"],
        "adzuna": {"app_id": "A", "app_key": "B"},
    }
    with patch.object(main, "SCRAPERS", [adz]), \
         patch.object(main, "_post_job", return_value="id"):
        main._scrape_for_config(cfg)
    assert adz.last_creds == {"app_id": "A", "app_key": "B"}


def test_public_sources_get_no_creds():
    muse = FakeScraper("the_muse", ["m"])
    cfg = {"user_id": "u1", "job_titles": ["eng"], "search_locations": ["Remote"], "adzuna": {"app_id": "A", "app_key": "B"}}
    with patch.object(main, "SCRAPERS", [muse]), \
         patch.object(main, "_post_job", return_value="id"):
        main._scrape_for_config(cfg)
    assert muse.last_creds is None


def test_no_keywords_short_circuits():
    adz = FakeScraper("adzuna", ["a"])
    cfg = {"user_id": "u1", "job_titles": [], "search_locations": ["Remote"], "adzuna": {"app_id": "A", "app_key": "B"}}
    with patch.object(main, "SCRAPERS", [adz]), \
         patch.object(main, "_post_job", return_value="id") as post:
        seen, created = main._scrape_for_config(cfg)
    assert (seen, created) == (0, 0)
    assert post.call_count == 0
