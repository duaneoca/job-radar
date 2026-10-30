"""llm_complete forwards a retry policy so transient provider hiccups (429/529)
auto-recover with backoff."""

import types

from app import llm


def test_llm_complete_passes_num_retries(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi"))]
        )

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)
    out = llm.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                           api_key="k", model="m")
    assert out == "hi"
    assert captured["num_retries"] == 2     # retry/backoff enabled
    assert captured["retry_strategy"] == "exponential_backoff_retry"


def test_a_failure_after_retries_surfaces_as_itself(monkeypatch):
    """The test above replaces litellm.completion, so it can't see litellm's own
    retry path — which imports tenacity only when a call fails. Without it, every
    failed call became "tenacity import failed" and the user never learned their
    key was bad or throttled. Run the REAL completion with a mocked provider error."""
    import pytest
    from fastapi import HTTPException

    real = llm.litellm.completion
    exc = llm.litellm.RateLimitError(message="slow down", llm_provider="anthropic",
                                     model="claude-haiku-4-5")
    monkeypatch.setattr(llm.litellm, "completion",
                        lambda **kw: real(**kw, mock_response=exc))
    with pytest.raises(HTTPException) as err:
        llm.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                         api_key="k", model="anthropic/claude-haiku-4-5")
    assert err.value.status_code == 429
    assert "tenacity" not in str(err.value.detail)
