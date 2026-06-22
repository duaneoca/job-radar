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
