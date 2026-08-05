"""Remembering permanent LLM key failures, so a user whose model was retired is
told instead of silently getting no scores.

The rule these tests exist to protect: only PERMANENT, user-fixable verdicts are
recorded. Recording a transient failure would tell a rate-limited user their model
is gone and send them to change a setting that was never wrong.
"""

from app import models
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID

STATUS_URL = f"/keys/internal/{TEST_USER_ID}/llm/status"
LLM_URL = f"/keys/internal/{TEST_USER_ID}/llm"


def _key(db, provider=models.LLMProvider.ANTHROPIC, model="claude-haiku-4-5"):
    row = models.UserAPIKey(
        user_id=TEST_USER_ID, provider=provider,
        encrypted_key=encrypt_api_key("sk-test"), preferred_model=model,
    )
    db.add(row)
    db.commit()
    return row


def _reload(db):
    return db.query(models.UserAPIKey).filter_by(user_id=TEST_USER_ID).first()


# ── the internal status endpoint ──────────────────────────────

def test_records_a_permanent_verdict(client, db):
    _key(db)
    r = client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "404 model not found"})
    assert r.status_code == 200

    key = _reload(db)
    assert key.last_error_kind == "invalid_model"
    assert "404" in key.last_error
    assert key.last_error_at is not None


def test_success_clears_a_recorded_verdict(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_key", "detail": "bad key"})
    r = client.post(STATUS_URL, json={"kind": None})
    assert r.status_code == 200

    key = _reload(db)
    assert key.last_error_kind is None
    assert key.last_error is None
    assert key.last_error_at is None


def test_rate_limited_is_recorded(client, db):
    """Not a rejection — the key and model are both fine. Recorded anyway,
    because a quota ceiling is the user's to act on and silence looks exactly
    like having nothing to score."""
    _key(db)
    r = client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429 RESOURCE_EXHAUSTED"})
    assert r.status_code == 200
    assert _reload(db).last_error_kind == "rate_limited"


def test_a_later_success_clears_rate_limited(client, db):
    """Throttling passes on its own, so the banner must not outlive it."""
    _key(db)
    client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429"})
    client.post(STATUS_URL, json={"kind": None})
    assert _reload(db).last_error_kind is None


def test_rate_limited_key_can_still_be_made_active(client, db):
    """Unlike a rejected key, a throttled one still works — refusing to select it
    would be telling the user to fix something that isn't broken."""
    _key(db)
    client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429"})
    assert client.put("/keys/active", json={"provider": "anthropic"}).status_code == 200


def test_provider_unavailable_is_recorded(client, db):
    """An outage is neither the user's fault nor the operator's problem, but the
    user should still know why nothing is being scored."""
    _key(db)
    r = client.post(STATUS_URL, json={"kind": "provider_unavailable", "detail": "Connection refused"})
    assert r.status_code == 200
    assert _reload(db).last_error_kind == "provider_unavailable"


def test_an_unreachable_provider_does_not_block_the_key(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "provider_unavailable", "detail": "timeout"})
    assert client.put("/keys/active", json={"provider": "anthropic"}).status_code == 200


def test_unknown_kind_is_rejected(client, db):
    """The column is a small closed set. A typo'd or invented kind would render
    as a blank banner the user can't act on."""
    _key(db)
    assert client.post(STATUS_URL, json={"kind": "quota_exceeded"}).status_code == 400
    assert _reload(db).last_error_kind is None


def test_status_404s_without_a_key(client, db):
    assert client.post(STATUS_URL, json={"kind": "invalid_model"}).status_code == 404


def test_long_detail_is_truncated(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "x" * 5000})
    assert len(_reload(db).last_error) == 1000


# ── what the worker fetches ───────────────────────────────────

def test_internal_llm_409s_when_no_model_selected(client, db):
    """409, not 404 — 404 already means "no key at all" and the worker skips
    quietly on that. This is a different, user-fixable state."""
    _key(db, model=None)
    r = client.get(LLM_URL)
    assert r.status_code == 409
    assert "model" in r.json()["detail"].lower()


def test_internal_llm_404s_when_no_key(client, db):
    assert client.get(LLM_URL).status_code == 404


def test_internal_llm_reports_whether_an_error_is_recorded(client, db):
    """Lets the worker skip the clear-my-error post-back on the overwhelming
    majority of reviews, where there was never an error to clear."""
    _key(db)
    assert client.get(LLM_URL).json()["last_error_kind"] is None

    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "gone"})
    assert client.get(LLM_URL).json()["last_error_kind"] == "invalid_model"


# ── clearing on user action ───────────────────────────────────

def test_choosing_a_new_model_clears_the_error(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "gone"})
    client.patch("/keys/anthropic", json={"preferred_model": "claude-sonnet-4-6"})
    assert _reload(db).last_error_kind is None


def test_replacing_the_key_clears_the_error(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_key", "detail": "401"})
    client.put("/keys", json={"provider": "anthropic", "api_key": "sk-ant-new9999"})
    assert _reload(db).last_error_kind is None


def test_resaving_a_key_does_not_wipe_the_model(client, db):
    """Regression: the Settings form re-saves a key without the model field, and
    `payload.preferred_model or None` used to null it. With no default model to
    fall back on, that silently broke every AI feature for the user."""
    _key(db, model="claude-haiku-4-5")
    client.put("/keys", json={"provider": "anthropic", "api_key": "sk-ant-rotated"})
    assert _reload(db).preferred_model == "claude-haiku-4-5"


def test_explicit_null_still_clears_the_model(client, db):
    """Sending the field explicitly is a deliberate clear, not an omission."""
    _key(db, model="claude-haiku-4-5")
    client.put("/keys", json={
        "provider": "anthropic", "api_key": "sk-ant-rotated", "preferred_model": None,
    })
    assert _reload(db).preferred_model is None


# ── what the user sees ────────────────────────────────────────

def test_list_keys_exposes_the_error(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "model gone"})
    row = next(k for k in client.get("/keys").json() if k["provider"] == "anthropic")
    assert row["last_error_kind"] == "invalid_model"
    assert row["last_error"] == "model gone"


# ── foreground AI features ────────────────────────────────────
# Tailoring, research and interview prep run in the request path, so the user
# already gets an error toast. The recorded state is what explains why their
# *background* scoring is also stalled — and it must reach the banner from every
# one of these, not just from the worker.

def _force(monkeypatch, exc):
    """Make the next llm_complete call fail with `exc` at the litellm boundary."""
    import litellm
    from app import llm as llm_mod

    def boom(**kwargs):
        raise exc
    monkeypatch.setattr(litellm, "completion", boom)
    return llm_mod


def _call(db, exc, monkeypatch):
    """Invoke llm_complete exactly as a foreground route does."""
    from fastapi import HTTPException
    import pytest as _pytest
    llm_mod = _force(monkeypatch, exc)
    with _pytest.raises(HTTPException) as caught:
        llm_mod.llm_complete(
            system="s", messages=[{"role": "user", "content": "x"}],
            api_key="k", model="m", db=db, user_id=TEST_USER_ID,
        )
    return caught.value


def test_foreground_dead_model_raises_the_banner(client, db, monkeypatch):
    """A retired model discovered while tailoring must not just show a toast and
    vanish — the next page load should still say something is wrong."""
    import litellm
    _key(db)
    err = _call(db, litellm.BadRequestError(
        message="model gemini-1.5-flash is not found", llm_provider="google", model="m"), monkeypatch)
    assert err.status_code == 400
    assert _reload(db).last_error_kind == "invalid_model"


def test_foreground_rate_limit_is_recorded(client, db, monkeypatch):
    import litellm
    _key(db)
    err = _call(db, litellm.RateLimitError(
        message="429 quota exceeded", llm_provider="google", model="m"), monkeypatch)
    assert err.status_code == 429
    assert _reload(db).last_error_kind == "rate_limited"


def test_foreground_timeout_is_an_outage_not_throttling(client, db, monkeypatch):
    """Same distinction as the worker: naming the wrong cause sends the user to
    change a quota that was never the problem."""
    import litellm
    _key(db)
    err = _call(db, litellm.Timeout(
        message="timed out", llm_provider="google", model="m"), monkeypatch)
    assert err.status_code == 502
    assert _reload(db).last_error_kind == "provider_unavailable"


def test_a_bug_in_our_own_code_is_not_blamed_on_the_provider(client, db, monkeypatch):
    """The catch-all must stay an operator problem: recording it would tell the
    user their provider is down when the fault is ours."""
    _key(db)
    err = _call(db, TypeError("we broke something"), monkeypatch)
    assert err.status_code == 502
    assert _reload(db).last_error_kind is None


def test_foreground_success_clears_a_recorded_failure(client, db, monkeypatch):
    """Tailoring working again should clear a banner the worker put up."""
    import litellm
    from app import llm as llm_mod
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "gone"})

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    monkeypatch.setattr(litellm, "completion", lambda **kw: _Resp())
    llm_mod.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                         api_key="k", model="m", db=db, user_id=TEST_USER_ID)
    assert _reload(db).last_error_kind is None


# ── unusable model output ─────────────────────────────────────
# A model that narrates instead of returning JSON. Counted, not reacted to:
# one rambling answer proves nothing, a streak is a model that won't comply.
# Same rule as retry-exhaustion for rate limits.

def test_a_single_unusable_response_says_nothing_to_the_user(client, db):
    _key(db)
    r = client.post(STATUS_URL, json={"kind": "unusable_output", "detail": "not json"})
    assert r.json()["status"] == "counted"
    assert _reload(db).last_error_kind is None      # no banner from one sample


def test_the_streak_raises_it(client, db):
    _key(db)
    for _ in range(models.UNUSABLE_OUTPUT_STREAK - 1):
        client.post(STATUS_URL, json={"kind": "unusable_output", "detail": "not json"})
        assert _reload(db).last_error_kind is None
    client.post(STATUS_URL, json={"kind": "unusable_output", "detail": "not json"})
    assert _reload(db).last_error_kind == "unusable_output"


def test_a_success_in_between_breaks_the_streak(client, db):
    """"Consecutive" has to mean consecutive, or the threshold is meaningless."""
    _key(db)
    client.post(STATUS_URL, json={"kind": "unusable_output"})
    client.post(STATUS_URL, json={"kind": "unusable_output"})
    client.post(STATUS_URL, json={"kind": None})               # one good review
    client.post(STATUS_URL, json={"kind": "unusable_output"})
    assert _reload(db).last_error_kind is None
    assert _reload(db).unusable_streak == 1


def test_a_different_failure_also_breaks_the_streak(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "unusable_output"})
    client.post(STATUS_URL, json={"kind": "rate_limited", "detail": "429"})
    assert _reload(db).unusable_streak == 0


def test_choosing_a_new_model_clears_the_streak(client, db):
    """The escape hatch. If this didn't reset, a user who switched models would
    still be one bad response away from an instant banner."""
    _key(db)
    for _ in range(models.UNUSABLE_OUTPUT_STREAK):
        client.post(STATUS_URL, json={"kind": "unusable_output"})
    assert _reload(db).last_error_kind == "unusable_output"

    client.patch("/keys/anthropic", json={"preferred_model": "claude-sonnet-4-6"})
    key = _reload(db)
    assert key.last_error_kind is None and key.unusable_streak == 0


def test_the_worker_is_told_to_stop(client, db):
    """The blocking contract: once recorded, the internal endpoint reports it so
    the worker skips instead of spending the user's quota to relearn it."""
    _key(db)
    for _ in range(models.UNUSABLE_OUTPUT_STREAK):
        client.post(STATUS_URL, json={"kind": "unusable_output"})
    assert client.get(LLM_URL).json()["last_error_kind"] == "unusable_output"
    assert "unusable_output" in models.KEY_ERRORS_BLOCKING


def test_an_unusable_key_can_still_be_made_active(client, db):
    """Selecting the key is how you get to the model dropdown that fixes it."""
    _key(db)
    for _ in range(models.UNUSABLE_OUTPUT_STREAK):
        client.post(STATUS_URL, json={"kind": "unusable_output"})
    assert client.put("/keys/active", json={"provider": "anthropic"}).status_code == 200


# ── truncation is ours, not the model's ───────────────────────
# A response cut off at max_tokens isn't a misbehaving model — it's a ceiling we
# chose. Interview prep failed this way from the day it shipped and reported
# "AI returned malformed JSON. Try regenerating", which is both wrong and
# unactionable: regenerating truncates in the same place.

def _truncated_response(content="{\"score\": 4.0, \"skills_rank\":"):
    class _Msg: pass
    class _Choice: pass
    class _Resp: pass
    m, ch, r = _Msg(), _Choice(), _Resp()
    m.content = content
    ch.message, ch.finish_reason = m, "length"
    r.choices = [ch]
    return r


def test_truncation_raises_a_clear_error_not_a_parser_complaint(client, db, monkeypatch):
    import litellm
    from fastapi import HTTPException
    import pytest as _pytest
    from app import llm as llm_mod
    _key(db)
    monkeypatch.setattr(litellm, "completion", lambda **kw: _truncated_response())
    with _pytest.raises(HTTPException) as caught:
        llm_mod.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                             api_key="k", model="m", db=db, user_id=TEST_USER_ID)
    assert caught.value.status_code == 502
    assert "cut off" in caught.value.detail.lower()


def test_truncation_is_not_recorded_against_the_key(client, db, monkeypatch):
    """It's our configuration. Recording it would raise a banner telling the user
    to change a model that was answering perfectly well."""
    import litellm
    from fastapi import HTTPException
    import pytest as _pytest
    from app import llm as llm_mod
    _key(db)
    monkeypatch.setattr(litellm, "completion", lambda **kw: _truncated_response())
    with _pytest.raises(HTTPException):
        llm_mod.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                             api_key="k", model="m", db=db, user_id=TEST_USER_ID)
    assert _reload(db).last_error_kind is None


def test_a_complete_response_is_unaffected(client, db, monkeypatch):
    import litellm
    from app import llm as llm_mod
    _key(db)
    resp = _truncated_response(content="all done")
    resp.choices[0].finish_reason = "stop"
    monkeypatch.setattr(litellm, "completion", lambda **kw: resp)
    out = llm_mod.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                               api_key="k", model="m", db=db, user_id=TEST_USER_ID)
    assert out == "all done"
