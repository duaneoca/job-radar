"""What happens to a job when the provider keeps saying 429.

The rule: a transient failure retries, but once it has outlasted every retry it
stops being an operator problem and becomes a user one. Two things must both be
true at that point — the user is told, and the exception does NOT escape the
task. If it escapes, Celery logs it at ERROR and one user's quota lands in the
operator's hourly digest, which is exactly backwards.
"""

import httpx
import pytest

from app import main as worker
from app.llm_errors import (
    INVALID_MODEL, PROVIDER_UNAVAILABLE, RATE_LIMITED, LLMCallFailed,
)

JOB_ID = "8bca15d1-3f2e-4a1b-9c8d-1122334455aa"
USER_ID = "f553ba2d-31c8-49e6-be6a-1b94119ce7b4"


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


@pytest.fixture
def wired(monkeypatch):
    """Stand in for tracker-api and the LLM; record every status post-back."""
    posted = []

    def fake_get(url, **kw):
        if "/jobs/internal/" in url:
            return _Resp({"title": "Eng", "company": "Acme", "description": "d"})
        if "/criteria/active" in url:
            return _Resp({"job_titles": ["Eng"]})
        if "/profile/active" in url:
            return _Resp({})
        if "/llm" in url:
            return _Resp({"api_key": "k", "model": "gemini/x", "last_error_kind": None})
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, **kw):
        if "/llm/status" in url:
            posted.append(kw["json"])
        return _Resp({})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    return posted


def _run(monkeypatch, retries: int, exc: Exception):
    """Run the task with a given retry count, as Celery would on attempt N."""
    class _Reviewer:
        def __init__(self, **kw):
            pass

        def review(self, **kw):
            raise exc

    monkeypatch.setattr(worker, "JobReviewer", _Reviewer)
    # called_directly=False makes Task.retry behave as it does inside a worker
    # (raise Retry). Called directly it re-raises the original exception instead,
    # which would let this test pass while proving nothing about production.
    worker.review_job.push_request(retries=retries, called_directly=False,
                                   id="test-task-id")
    try:
        return worker.review_job.run(JOB_ID, USER_ID)
    finally:
        worker.review_job.pop_request()


# ── still has retries left ────────────────────────────────────

@pytest.mark.parametrize("retries", [0, 1, 2])
def test_transient_failure_reports_nothing_while_retries_remain(monkeypatch, wired, retries):
    """A 429 mid-burst is normal and self-healing. Nagging the user here would
    put a banner up during ordinary catch-up scoring.

    Asserts only that nothing was reported. Whether the task then raises Retry or
    re-raises the original is Celery's business, and depends on a reachable
    broker — pinning it here would test Celery, not us.
    """
    with pytest.raises(Exception):
        _run(monkeypatch, retries, LLMCallFailed(None, "429 RESOURCE_EXHAUSTED"))
    assert wired == []


def test_transient_failure_does_not_swallow_the_job_while_retries_remain(monkeypatch, wired):
    """It must raise rather than return: a silent return would mark the job done
    and it would never be scored."""
    with pytest.raises(Exception):
        _run(monkeypatch, 0, LLMCallFailed(None, "429"))


# ── retries exhausted ─────────────────────────────────────────

def test_exhausted_retries_report_rate_limited(monkeypatch, wired):
    _run(monkeypatch, 3, LLMCallFailed(None, "429 RESOURCE_EXHAUSTED", RATE_LIMITED))
    assert wired == [{"kind": "rate_limited", "detail": "429 RESOURCE_EXHAUSTED"}]


def test_an_outage_is_not_reported_as_throttling(monkeypatch, wired):
    """Regression: every exhausted transient used to be labelled rate_limited,
    so a provider outage told the user to raise a quota."""
    _run(monkeypatch, 3, LLMCallFailed(None, "Connection refused", PROVIDER_UNAVAILABLE))
    assert wired == [{"kind": "provider_unavailable", "detail": "Connection refused"}]


def test_a_flavourless_transient_falls_back_to_outage(monkeypatch, wired):
    """"Not responding" is vague but never wrong; claiming a quota problem is a
    specific assertion about the user's account."""
    _run(monkeypatch, 3, LLMCallFailed(None, "mystery"))
    assert wired[0]["kind"] == "provider_unavailable"


def test_exhausted_retries_do_not_raise(monkeypatch, wired):
    """The whole point. If this escapes, Celery logs ERROR and the digest mails
    the operator about a user's quota."""
    assert _run(monkeypatch, 3, LLMCallFailed(None, "429")) is None


def test_beyond_max_retries_still_does_not_raise(monkeypatch, wired):
    assert _run(monkeypatch, 99, LLMCallFailed(None, "429")) is None


# ── permanent failures are unchanged ──────────────────────────

def test_permanent_failure_reports_its_own_kind_on_the_first_try(monkeypatch, wired):
    """A dead model must not wait three minutes to be reported, and must never
    be relabelled as throttling."""
    _run(monkeypatch, 0, LLMCallFailed(INVALID_MODEL, "404 model not found"))
    assert wired == [{"kind": "invalid_model", "detail": "404 model not found"}]


def test_permanent_failure_at_max_retries_is_still_permanent(monkeypatch, wired):
    _run(monkeypatch, 3, LLMCallFailed(INVALID_MODEL, "404 model not found"))
    assert wired[0]["kind"] == "invalid_model"


# ── truncation is ours, not the model's ───────────────────────

def test_truncated_response_is_not_blamed_on_the_user(monkeypatch, wired):
    """A response cut off at OUR max_tokens must not be reported as
    unusable_output — three of those raise a banner telling the user to switch
    models because of our configuration."""
    from app.llm_errors import ResponseTruncated
    _run(monkeypatch, 0, ResponseTruncated("truncated at max_tokens=4096"))
    assert wired == []


def test_unparseable_output_is_still_reported(monkeypatch, wired):
    """The contrast: the model finished and what it said was unusable. That IS
    theirs, and it is what the streak counts."""
    class _Reviewer:
        def __init__(self, **kw): pass
        def review(self, **kw): return None

    monkeypatch.setattr(worker, "JobReviewer", _Reviewer)
    worker.review_job.push_request(retries=0, called_directly=False, id="t")
    try:
        worker.review_job.run(JOB_ID, USER_ID)
    finally:
        worker.review_job.pop_request()
    assert wired == [{"kind": "unusable_output",
                      "detail": "model gemini/x did not return usable JSON"}]
