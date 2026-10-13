"""JSearch mapping, query budgeting, and the 24h per-user throttle."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

from app import main
from app.scrapers.jsearch import (
    JSearchScraper, RunThrottle, _RUN_GRACE, _build_query, _extract_items, _to_raw_job, stable_id,
)

ITEM = {
    "job_id": "abc123==",
    "job_title": "Forward Deployed Engineer",
    "employer_name": "Anthropic",
    "job_city": "San Francisco",
    "job_state": "CA",
    "job_is_remote": True,
    "job_description": "Deploy Claude for customers...",
    "job_apply_link": "https://www.linkedin.com/jobs/view/999",
    "job_min_salary": 180000,
    "job_max_salary": 250000,
    "job_salary_period": "YEAR",
    "job_posted_at_datetime_utc": "2026-07-08T00:00:00.000Z",
}


class FakeThrottle:
    def __init__(self, allow=True):
        self._allow = allow
        self.calls = []

    def allow(self, user_id):
        self.calls.append(user_id)
        return self._allow


def test_mapping():
    raw = _to_raw_job(ITEM)
    assert raw.external_id == stable_id(ITEM["job_apply_link"])
    assert raw.source == "jsearch"
    assert raw.company == "Anthropic"
    assert raw.location == "San Francisco, CA"
    assert raw.remote is True
    assert (raw.salary_min, raw.salary_max) == (180000, 250000)
    assert raw.date_posted == "2026-07-08T00:00:00.000Z"


def test_hourly_salary_not_annualized():
    raw = _to_raw_job({**ITEM, "job_salary_period": "HOUR",
                       "job_min_salary": 80, "job_max_salary": 120})
    assert raw.salary_min is None and raw.salary_max is None


def test_query_or_combined_with_location():
    query, params = _build_query(["FDE", "Solutions Architect"], "Austin, TX")
    assert params["query"] == "FDE OR Solutions Architect in Austin, TX"
    assert params["date_posted"] == "week"
    assert "work_from_home" not in params
    # /search-v2 is cursor-paginated; we take the first page by sending neither
    # a cursor nor the retired page/num_pages params.
    assert not {"cursor", "page", "num_pages"} & params.keys()


def test_extract_items_accepts_both_data_shapes():
    assert _extract_items({"data": [ITEM]}) == [ITEM]                    # bare array
    assert _extract_items({"data": {"jobs": [ITEM], "cursor": "x"}}) == [ITEM]
    assert _extract_items({"data": {"postings": [ITEM]}}) == [ITEM]     # unknown key
    assert _extract_items({"data": {"cursor": "x"}}) == []
    assert _extract_items({}) == []


def test_url_falls_back_to_apply_options():
    item = {**ITEM, "job_apply_link": None,
            "apply_options": [{"publisher": "LinkedIn", "apply_link": "https://li.example/1"}]}
    assert _to_raw_job(item).url == "https://li.example/1"


def test_query_remote_uses_flag_not_location():
    _, params = _build_query(["FDE"], "Remote")
    assert params["query"] == "FDE"
    assert params["work_from_home"] == "true"


def test_no_creds_skips():
    scraper = JSearchScraper(throttle=FakeThrottle())
    assert asyncio.run(scraper.scrape(["FDE"], "Remote", None)) == []
    assert asyncio.run(scraper.scrape(["FDE"], "Remote", {"api_key": "k"})) == []  # no user_id


def test_throttled_user_skipped_without_http():
    throttle = FakeThrottle(allow=False)
    scraper = JSearchScraper(throttle=throttle)
    with patch("app.scrapers.jsearch.httpx.AsyncClient") as client_cls:
        jobs = asyncio.run(scraper.scrape(
            ["FDE"], "Remote", {"api_key": "k", "user_id": "u1"}))
    assert jobs == []
    assert throttle.calls == ["u1"]
    client_cls.assert_not_called()


def test_run_throttle_local_semantics():
    """First call claims the window; same-run calls pass; a later run is blocked."""
    t = RunThrottle.__new__(RunThrottle)
    t._redis = None
    t._local = {}
    assert t.allow("u1") is True           # claims the window
    assert t.allow("u1") is True           # same run (within grace) — other locations
    t._local[RunThrottle._key("u1")] = time.time() - (_RUN_GRACE + 1)
    assert t.allow("u1") is False          # a previous run inside the 24h window


def test_scrape_maps_response():
    scraper = JSearchScraper(throttle=FakeThrottle())

    class Resp:
        status_code = 200
        headers = {}
        def json(self):
            return {"data": [ITEM, ITEM]}  # duplicate → deduped

    fake_client = AsyncMock()
    fake_client.get.return_value = Resp()
    fake_client.__aenter__.return_value = fake_client
    with patch("app.scrapers.jsearch.httpx.AsyncClient", return_value=fake_client):
        jobs = asyncio.run(scraper.scrape(
            ["FDE"], "Austin, TX", {"api_key": "k", "user_id": "u1"}))
    assert len(jobs) == 1
    assert jobs[0].external_id == stable_id(ITEM["job_apply_link"])
    headers = fake_client.get.call_args.kwargs["headers"]
    assert headers["X-RapidAPI-Key"] == "k"


def test_creds_for_jsearch():
    scraper = JSearchScraper(throttle=FakeThrottle())
    run, creds = main._creds_for(scraper, {"user_id": "u1", "jsearch_api_key": "k"})
    assert run and creds == {"api_key": "k", "user_id": "u1"}
    run, creds = main._creds_for(scraper, {"user_id": "u1"})
    assert not run


def test_locations_capped_for_jsearch():
    scraper = JSearchScraper(throttle=FakeThrottle())
    locs = ["A", "B", "C", "D", "E"]
    assert main._locations_for(scraper, locs) == ["A", "B", "C"]
    assert main._locations_for(main.SCRAPERS[0], locs) == locs  # adzuna uncapped


# ── stable ids (production, 2026-10: 372 of 539 JSearch jobs were exact-link
# copies — JSearch re-issues job_id for the same posting on different days) ──

def test_same_link_new_job_id_is_the_same_job():
    a = _to_raw_job({**ITEM, "job_id": "day-one=="})
    b = _to_raw_job({**ITEM, "job_id": "day-two=="})
    assert a.external_id == b.external_id


def test_tracking_parameters_do_not_split_a_posting():
    base = "https://careers.servicenow.com/jobs/744000144639119/forward-deployed"
    assert stable_id(base) == stable_id(base + "/?utm_source=jsearch&src=google&trk=x")
    assert stable_id(base) == stable_id(base.replace("https://", "https://www."))


def test_identifying_parameters_keep_postings_apart():
    """Indeed's links differ only by jk= — dropping it would merge every Indeed job."""
    a = stable_id("https://www.indeed.com/viewjob?jk=4043eff68d6c552c")
    b = stable_id("https://www.indeed.com/viewjob?jk=1111aaaa2222bbbb")
    assert a != b


def test_no_link_falls_back_to_jsearch_id():
    item = {k: v for k, v in ITEM.items() if k != "job_apply_link"}
    assert _to_raw_job(item).external_id == "abc123=="


def test_id_fits_the_column_however_long_the_link():
    assert len(stable_id("https://example.com/" + "x" * 3000)) <= 255
