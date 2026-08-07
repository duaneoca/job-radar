"""Which providers we ask for JSON at the API level.

Anthropic reports response_format as "supported" through litellm, but implements
it by forcing a tool call with an EMPTY schema. Claude then fills that tool with
whatever fields suit the input and ignores the prompt's output format. Measured
in production against claude-haiku-4-5, same prompt, one variable:

    without → {"score": 0.5, "summary": "…"}         correct
    with    → {"position": "Senior Engineer", …}     wrong keys

The JSON parses; it just isn't ours. That scored nothing for a real user for a
day and then told them their model was broken.

Keyed on PROVIDER, never on model names: the behaviour belongs to litellm's
adapter for a provider, and a list of model strings is stale the day a model is
retired — the same rot that made us remove the default model.
"""

import pytest

from app.reviewer import _supports_json_mode


@pytest.mark.parametrize("provider", ["openai", "google", "groq"])
def test_native_json_mode_providers_are_asked(provider):
    assert _supports_json_mode(provider) is True


def test_case_is_not_significant():
    """The value comes over HTTP from another service; don't let casing decide."""
    assert _supports_json_mode("OpenAI") is True


def test_anthropic_is_never_asked():
    """The regression. litellm says "supported"; the result is unusable."""
    assert _supports_json_mode("anthropic") is False


@pytest.mark.parametrize("provider", [None, "", "some-new-provider", "ollama"])
def test_unknown_providers_fall_through_to_prompt_only(provider):
    """Fails to the behaviour that works everywhere. Sending an emulated
    parameter is the failure mode, not omitting it."""
    assert _supports_json_mode(provider) is False


def test_gate_values_are_real_providers():
    """A typo here silently disables JSON mode for a whole provider — nothing
    errors, answers just get slightly worse. These must be LLMProvider values.
    Spelled out rather than imported: ai-reviewer shares no package with
    tracker-api, so this list is the contract between them.
    """
    from app.reviewer import _NATIVE_JSON_MODE_PROVIDERS
    assert _NATIVE_JSON_MODE_PROVIDERS <= {"anthropic", "openai", "google", "groq"}
