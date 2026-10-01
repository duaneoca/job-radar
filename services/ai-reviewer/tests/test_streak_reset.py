"""A success must reset the unusable-output count.

The count is meant to be CONSECUTIVE: one rambling answer proves nothing, three
in a row is a model that won't comply. But the worker only reported a success
when a verdict was already recorded — while answers were merely being counted
there was none, so nothing reset the count between bad answers. "Three in a row"
became "three ever", and a model failing one review in a few hundred eventually
got blamed and had scoring stopped (seen on staging, 2026-10-01: streak 4, with
production scoring 328 of 328 cleanly that day).
"""

import httpx
import pytest

from app import main as worker
from app.reviewer import ReviewResult

JOB_ID = "8bca15d1-3f2e-4a1b-9c8d-1122334455aa"
USER_ID = "f553ba2d-31c8-49e6-be6a-1b94119ce7b4"


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _wire(monkeypatch, llm_payload):
    posted = []

    def fake_get(url, **kw):
        if "/jobs/internal/" in url:
            return _Resp({"title": "Eng", "company": "Acme", "description": "d"})
        if "/criteria/active" in url:
            return _Resp({"job_titles": ["Eng"]})
        if "/profile/active" in url:
            return _Resp({})
        if "/llm" in url:
            return _Resp({"api_key": "k", "model": "anthropic/x", **llm_payload})
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, **kw):
        if "/llm/status" in url:
            posted.append(kw["json"])
        return _Resp({})

    class _Reviewer:
        def __init__(self, **kw):
            pass

        def review(self, **kw):
            return ReviewResult(job_id=JOB_ID, score=7.0, skills_rank=7, experience_rank=7,
                                location_rank=7, education_rank=7, salary_rank=7,
                                summary="s", pros=[], cons=[], recommended=True)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(worker, "JobReviewer", _Reviewer)
    worker.review_job.push_request(retries=0, called_directly=False, id="t")
    try:
        worker.review_job.run(JOB_ID, USER_ID)
    finally:
        worker.review_job.pop_request()
    return posted


def test_success_while_counting_resets_the_count(monkeypatch):
    posted = _wire(monkeypatch, {"last_error_kind": None, "unusable_streak": 2})
    assert {"kind": None} in [{"kind": p.get("kind")} for p in posted]


def test_success_with_nothing_to_reset_makes_no_round_trip(monkeypatch):
    """The reason the post-back is conditional at all: a large backlog."""
    assert _wire(monkeypatch, {"last_error_kind": None, "unusable_streak": 0}) == []


@pytest.mark.parametrize("payload", [{}, {"last_error_kind": None, "unusable_streak": None}])
def test_older_tracker_api_without_the_field(monkeypatch, payload):
    """ai-reviewer may deploy before tracker-api; a missing count means none."""
    assert _wire(monkeypatch, payload) == []
