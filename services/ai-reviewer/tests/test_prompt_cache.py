"""Prompt caching for scoring.

A scrape scores a burst of jobs for one user, and everything but the posting is
identical across it. Anthropic caches that prefix only when a cache_control
breakpoint asks it to; OpenAI and Gemini cache a repeated prefix on their own.

A broken cache raises nothing — the request simply costs full price — so these
tests pin the three things that silently break it: the breakpoint reaching the
wire, the per-job text staying after it, and the cached block staying identical
from one job to the next.
"""

import litellm
import pytest

from app.reviewer import (
    JobReviewer, _PROMPT_CACHE_PROVIDERS, _supports_prompt_cache, build_messages,
    cache_usage,
)

CRITERIA = {"job_titles": ["SRE"], "required_skills": ["k8s"], "remote_only": True,
            "min_salary": 140000}
PROFILE = {"name": "D", "skills": ["python"], "resume_text": "Ten years of ops."}


def _posting(title="SRE", description="Run k8s."):
    return JobReviewer._build_job_posting(
        job_title=title, company="Acme", location="Toronto", remote=True,
        description=description, salary_min=150000, salary_max=None,
    )


def test_only_anthropic_gets_a_breakpoint():
    assert _supports_prompt_cache("anthropic") is True
    assert _supports_prompt_cache("Anthropic") is True


@pytest.mark.parametrize("provider", ["openai", "google", "groq", None, "", "ollama"])
def test_other_providers_get_no_breakpoint(provider):
    """Only litellm's Anthropic adapter knows cache_control (1.57.3). The rest
    would forward it to an API that never asked for it."""
    assert _supports_prompt_cache(provider) is False
    msgs = build_messages("SYS", "CAND", "POST", provider)
    assert msgs[1]["content"] == "CAND\n\nPOST"
    assert "cache_control" not in repr(msgs)


def test_gate_values_are_real_providers():
    """These must be LLMProvider values — a typo silently disables caching."""
    assert _PROMPT_CACHE_PROVIDERS <= {"anthropic", "openai", "google", "groq"}


def test_breakpoint_reaches_the_anthropic_request_body():
    """Through litellm's real Anthropic transform, not our own dicts: the
    breakpoint must land on the candidate block, with the posting after it."""
    msgs = build_messages("SYS", "CAND", "POST", "anthropic")
    body = litellm.AnthropicConfig().transform_request(
        model="claude-sonnet-4-6", messages=msgs,
        optional_params={"max_tokens": 10}, litellm_params={}, headers={},
    )
    assert body["system"] == [{"type": "text", "text": "SYS"}]
    blocks = body["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "CAND",
                         "cache_control": {"type": "ephemeral"}}
    assert blocks[1] == {"type": "text", "text": "POST"}


def test_cached_block_is_identical_across_jobs():
    """Any per-job byte in the candidate block turns every read into a write."""
    r = JobReviewer("k", "m", "anthropic")
    a = build_messages("SYS", r._build_candidate_context(CRITERIA, PROFILE),
                       _posting("SRE", "Run k8s."), "anthropic")
    b = build_messages("SYS", r._build_candidate_context(CRITERIA, PROFILE),
                       _posting("Platform Engineer", "Own the CI."), "anthropic")
    assert a[0] == b[0]
    assert a[1]["content"][0] == b[1]["content"][0]
    assert a[1]["content"][1] != b[1]["content"][1]


def test_review_sends_the_cached_shape(monkeypatch):
    """End to end through review(): the call litellm actually receives."""
    seen = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return litellm.ModelResponse(choices=[{"message": {"content": (
            '{"score": 7, "skills_rank": 7, "experience_rank": 7, "location_rank": 7,'
            ' "education_rank": 7, "salary_rank": 7, "summary": "s", "pros": [],'
            ' "cons": [], "recommended": true}')}}])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = JobReviewer("k", "anthropic/claude-sonnet-4-6", "anthropic").review(
        job_id="j1", job_title="SRE", company="Acme", description="Run k8s.",
        criteria=CRITERIA, profile=PROFILE,
    )
    assert result is not None
    user = seen["messages"][1]["content"]
    assert "cache_control" in user[0] and "Ten years of ops." in user[0]["text"]
    assert user[1]["text"].startswith("## Job Posting")


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_cache_usage_reads_litellm_usage():
    resp = _Obj(usage=_Obj(prompt_tokens=3000, cache_creation_input_tokens=0,
                           prompt_tokens_details=_Obj(cached_tokens=2400)))
    assert cache_usage(resp) == (3000, 2400, 0)


@pytest.mark.parametrize("resp", [
    _Obj(),                                              # no usage at all
    _Obj(usage=_Obj(prompt_tokens=None)),                # nothing reported
    _Obj(usage=_Obj(prompt_tokens=10, prompt_tokens_details=None)),
])
def test_cache_usage_tolerates_silent_providers(resp):
    """A logging helper must never fail a review that succeeded."""
    in_tok, read, write = cache_usage(resp)
    assert (read, write) == (0, 0)
