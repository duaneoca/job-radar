"""Dispatch tests for the company-board pass in _scrape_for_config."""

from unittest.mock import patch

from app import main


class FakeCompanyScraper:
    def __init__(self, name, jobs):
        self.source_name = name
        self._jobs = jobs
        self.calls = []  # (companies, keywords) per invocation

    async def scrape_companies(self, companies, keywords):
        self.calls.append((list(companies), list(keywords)))
        return list(self._jobs)


def test_company_pass_runs_once_despite_many_locations():
    board = FakeCompanyScraper("greenhouse", ["j1", "j2"])
    cfg = {
        "user_id": "u1", "job_titles": ["eng"],
        "search_locations": ["Austin, TX", "Denver, CO", "Remote"],
        "target_companies": ["Acme"], "adzuna": None,
    }
    with patch.object(main, "SCRAPERS", []), \
         patch.object(main, "COMPANY_SCRAPERS", [board]), \
         patch.object(main, "_post_job", return_value="id") as post:
        seen, created = main._scrape_for_config(cfg)
    assert len(board.calls) == 1  # once per user, NOT per location
    assert board.calls[0] == (["Acme"], ["eng"])
    assert (seen, created) == (2, 2)
    assert all(c.kwargs.get("user_id") == "u1" for c in post.call_args_list)


def test_company_pass_skipped_without_target_companies():
    board = FakeCompanyScraper("greenhouse", ["j1"])
    for cfg in (
        {"user_id": "u1", "job_titles": ["eng"], "search_locations": ["Remote"], "adzuna": None},
        {"user_id": "u1", "job_titles": ["eng"], "search_locations": ["Remote"],
         "target_companies": [], "adzuna": None},
    ):
        with patch.object(main, "SCRAPERS", []), \
             patch.object(main, "COMPANY_SCRAPERS", [board]), \
             patch.object(main, "_post_job", return_value="id"):
            main._scrape_for_config(cfg)
    assert board.calls == []


def test_company_pass_crash_isolated():
    class Crasher:
        source_name = "ashby"
        async def scrape_companies(self, companies, keywords):
            raise RuntimeError("boom")

    ok = FakeCompanyScraper("lever", ["j1"])
    cfg = {"user_id": "u1", "job_titles": ["eng"], "search_locations": ["Remote"],
           "target_companies": ["Acme"], "adzuna": None}
    with patch.object(main, "SCRAPERS", []), \
         patch.object(main, "COMPANY_SCRAPERS", [Crasher(), ok]), \
         patch.object(main, "_post_job", return_value="id"):
        seen, created = main._scrape_for_config(cfg)
    assert (seen, created) == (1, 1)  # lever still ran after ashby crashed
