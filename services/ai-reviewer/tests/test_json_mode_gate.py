"""Which models we ask for JSON at the API level.

Anthropic reports response_format as "supported" through litellm, but implements
it by forcing a tool call with an EMPTY schema. Claude then fills that tool with
whatever fields suit the input and ignores the prompt's output format. Measured
in production against claude-haiku-4-5, same prompt, one variable:

    without → {"score": 0.5, "summary": "…"}         correct
    with    → {"position": "Senior Engineer", …}     wrong keys

The JSON parses; it just isn't ours. That scored nothing for a real user for a
day and then told them their model was broken.
"""

import pytest

from app.reviewer import _supports_json_mode


@pytest.mark.parametrize("model", [
    "gpt-4o-mini", "gpt-4o", "o1-mini", "o3", "chatgpt-4o-latest",
    "gemini/gemini-3.5-flash", "gemini/gemini-2.5-pro",
    "groq/llama-3.3-70b-versatile",
])
def test_native_json_mode_providers_are_asked(model):
    assert _supports_json_mode(model) is True


@pytest.mark.parametrize("model", [
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",     # the exact string that broke in production
    "claude-sonnet-4-6",
    "claude-opus-4-7",
])
def test_anthropic_is_never_asked(model):
    """The regression. litellm says "supported"; the result is unusable."""
    assert _supports_json_mode(model) is False


@pytest.mark.parametrize("model", ["", "some-new-provider/model-x", "llama-local"])
def test_unknown_models_fall_through_to_prompt_only(model):
    """Fails to the behaviour that works everywhere. Sending an emulated
    parameter is the failure mode, not omitting it."""
    assert _supports_json_mode(model) is False
