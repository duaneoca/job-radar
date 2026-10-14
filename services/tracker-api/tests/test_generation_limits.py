"""How long each generated answer may be.

Research, application answers, refine and extract-changes ran on llm_complete's
1024-token default. With Tavily context, research outgrew it: on 2026-10-14, 4 of
6 research requests in production were cut off and thrown away, and the user was
told to shorten their prompt. Only generated tokens are billed, so a roomy ceiling
costs nothing when an answer finishes early.
"""

from app.routers import generate as generate_mod

from .test_writing_skills import _scrape, _seed


def _capture(monkeypatch):
    seen = {}

    def fake(system, messages, api_key, model, max_tokens=1024, **kwargs):
        seen["max_tokens"] = max_tokens
        return '{"questions": [], "changes": []}'
    monkeypatch.setattr(generate_mod, "llm_complete", fake)
    return seen


def _limit(client, monkeypatch, path, **body):
    seen = _capture(monkeypatch)
    client.post(path, json=body or None)
    return seen.get("max_tokens")


def test_generated_text_has_room(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    for path, body in [
        (f"/jobs/{rid}/research", {}),
        (f"/jobs/{rid}/application/0", {}),
        (f"/jobs/{rid}/refine", {"messages": [{"role": "user", "content": "shorter"}], "template_idx": 0}),
        (f"/jobs/{rid}/extract-changes", {"change_type": "voice", "current_content": "",
                                          "messages": [{"role": "user", "content": "x"}]}),
    ]:
        assert _limit(client, monkeypatch, path, **body) == generate_mod.GENERATION_MAX_TOKENS, path
    assert generate_mod.GENERATION_MAX_TOKENS >= 4096


def test_interview_prep_keeps_its_larger_ceiling(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    assert _limit(client, monkeypatch, f"/jobs/{rid}/interview-prep") == 8192


def test_truncation_no_longer_blames_the_users_prompt():
    import inspect
    from app import llm
    src = inspect.getsource(llm.llm_complete)
    assert "try shortening your custom prompt" not in src
