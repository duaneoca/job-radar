"""Permanent-vs-transient classification of LLM failures.

Getting this backwards is worse than not classifying at all. A permanent verdict
stops retries, is written to the user's key, and raises a banner telling them to
change a setting. Do that to a rate-limited user and you've sent them to fix
something that was never broken — so every test here that asserts `is None` is
protecting a user from bad advice, not just checking a branch.
"""

import litellm
import pytest

from app.llm_errors import INVALID_KEY, INVALID_MODEL, LLMCallFailed, classify_llm_error


def _err(cls, message, **kw):
    """Build a real litellm exception. Constructor signatures vary by class, so
    fall back to bypassing __init__ rather than asserting on litellm internals."""
    try:
        return cls(message=message, llm_provider="google", model="gemini/x", **kw)
    except TypeError:
        exc = cls.__new__(cls)
        Exception.__init__(exc, message)
        return exc


# ── transient: retry, record nothing ──────────────────────────

@pytest.mark.parametrize("cls", [
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.APIConnectionError,
])
def test_transient_types_are_never_permanent(cls):
    assert classify_llm_error(_err(cls, "boom")) is None


def test_rate_limit_mentioning_invalid_is_still_transient():
    """The reason exception TYPE is checked before message text. Google's 429 body
    is verbose and can easily contain "invalid"; reading that as a retired model
    would tell a throttled user to change a setting that is correct."""
    exc = _err(
        litellm.RateLimitError,
        "429 RESOURCE_EXHAUSTED: quota exceeded for model gemini-2.5-flash; "
        "request was invalid for the current quota tier and not found in cache",
    )
    assert classify_llm_error(exc) is None


def test_overloaded_5xx_is_transient():
    assert classify_llm_error(_err(litellm.InternalServerError, "529 model overloaded")) is None


def test_context_window_exceeded_is_not_a_key_problem():
    """One job's description was too long. The key and model are both fine."""
    exc = _err(litellm.ContextWindowExceededError, "This model's maximum context length is 8192 tokens")
    assert classify_llm_error(exc) is None


def test_unknown_errors_default_to_transient():
    """Unknown is grouped with transient on purpose: a wrong retry costs one API
    call, a wrong banner costs the user's trust in every future banner."""
    assert classify_llm_error(RuntimeError("something we've never seen")) is None


# ── permanent: stop, record, show a banner ────────────────────

def test_auth_error_is_an_invalid_key():
    assert classify_llm_error(_err(litellm.AuthenticationError, "401")) == INVALID_KEY


def test_api_key_wording_is_an_invalid_key():
    assert classify_llm_error(RuntimeError("Your API key is not valid")) == INVALID_KEY


@pytest.mark.parametrize("message", [
    "404 models/gemini-1.5-flash is not found for API version v1beta",
    "The model `claude-2` is deprecated",
    "invalid model id supplied",
    "model does not exist",
])
def test_retired_model_wording_is_an_invalid_model(message):
    assert classify_llm_error(_err(litellm.BadRequestError, message)) == INVALID_MODEL


def test_bare_404_is_an_invalid_model():
    """A chat-completions URL has nothing else in it that could be missing."""
    assert classify_llm_error(_err(litellm.NotFoundError, "404")) == INVALID_MODEL


def test_bad_request_unrelated_to_the_model_is_not_permanent():
    exc = _err(litellm.BadRequestError, "temperature must be between 0 and 1")
    assert classify_llm_error(exc) is None


# ── the exception the worker branches on ──────────────────────

def test_llm_call_failed_reports_permanence():
    assert LLMCallFailed(INVALID_MODEL, "gone").permanent is True
    assert LLMCallFailed(None, "timeout").permanent is False
