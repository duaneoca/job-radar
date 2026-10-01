"""Two models per key: ANALYSIS (output only the user reads) and WRITING (text an
employer may read).

The rules these tests protect:
  * an unset writing model means the user's own analysis model — never ours;
  * each feature uses the right one;
  * changing one model never wipes the other;
  * a verdict about one model never blocks, or gets cleared by, the other.
    A retired writing model must not stop scoring, and scoring working must not
    erase a real writing failure.
"""

from unittest.mock import patch

from app import models
from app.llm import error_applies_to, get_llm_provider, model_for_key
from app.routers import generate as generate_mod
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID

ANALYSIS, WRITING = models.MODEL_PURPOSE_ANALYSIS, models.MODEL_PURPOSE_WRITING
HAIKU, SONNET = "claude-haiku-4-5", "claude-sonnet-4-6"
STATUS_URL = f"/keys/internal/{TEST_USER_ID}/llm/status"
LLM_URL = f"/keys/internal/{TEST_USER_ID}/llm"
JOB = {"title": "Data Eng", "company": "Globex", "url": "https://x/1", "source": "manual",
       "description": "Build pipelines."}


def _key(db, analysis=HAIKU, writing=SONNET):
    row = models.UserAPIKey(
        user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
        encrypted_key=encrypt_api_key("sk-test"),
        preferred_model=analysis, writing_model=writing,
    )
    db.add(row)
    db.commit()
    return row


def _reload(db):
    return db.query(models.UserAPIKey).filter_by(user_id=TEST_USER_ID).first()


def _record(db, kind, model):
    from app.llm import record_key_error
    record_key_error(db, _reload(db), kind, "detail", model)


# ── which model ───────────────────────────────────────────────

def test_unset_writing_model_is_the_users_analysis_model(db, test_user):
    """Not a default of ours — the user's own choice, so existing keys behave
    exactly as before the split."""
    key = _key(db, writing=None)
    assert model_for_key(key, WRITING) == HAIKU
    assert model_for_key(key, ANALYSIS) == HAIKU


def test_each_purpose_gets_its_model(db, test_user):
    _key(db)
    assert get_llm_provider(TEST_USER_ID, db)[1] == HAIKU
    assert get_llm_provider(TEST_USER_ID, db, WRITING)[1] == SONNET


def test_writing_model_does_not_stand_in_for_a_missing_analysis_model(db, test_user):
    """No default model, in either direction."""
    assert model_for_key(_key(db, analysis=None), ANALYSIS) is None


def test_scoring_config_uses_the_analysis_model(client, db):
    _key(db)
    assert client.get(LLM_URL).json()["model"] == HAIKU


def _features(client, db, monkeypatch):
    """{endpoint: model} for every foreground AI feature, through the real routes."""
    db.add(models.Profile(user_id=TEST_USER_ID, name="default", is_active=True,
                          resume_text="I build pipelines."))
    db.add(models.Criteria(user_id=TEST_USER_ID, name="default", is_active=True,
                           job_titles=["Data Eng"]))
    db.commit()
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    rid = client.get("/jobs").json()["items"][0]["id"]

    used = {}

    def fake(system, messages, api_key, model, max_tokens=1024, **kwargs):
        used["last"] = model
        return '{"questions": []}'
    monkeypatch.setattr(generate_mod, "llm_complete", fake)

    seen = {}
    for name, path in [
        ("research", f"/jobs/{rid}/research"),
        ("application", f"/jobs/{rid}/application/0"),
        ("interview_prep", f"/jobs/{rid}/interview-prep"),
    ]:
        used.clear()
        client.post(path)
        seen[name] = used.get("last")
    return seen


def test_features_use_the_right_model(client, db, monkeypatch):
    _key(db)
    seen = _features(client, db, monkeypatch)
    assert seen["research"] == HAIKU            # only the user reads it
    assert seen["application"] == SONNET        # an employer reads it
    assert seen["interview_prep"] == SONNET     # said aloud to an interviewer


def test_writing_routes_ask_for_the_writing_model():
    """The refine and tailor routes need a full résumé pipeline to exercise, so
    pin them at the source: every employer-facing route asks for WRITING and
    nothing else does. A new route that forgets shows up here."""
    import inspect
    src = inspect.getsource(generate_mod)
    writing_routes = {"generate_application_answer", "refine_application",
                      "generate_interview_prep", "tailor_resume_endpoint",
                      "refine_tailored_resume"}
    for name, fn in inspect.getmembers(generate_mod, inspect.isfunction):
        if fn.__module__ != generate_mod.__name__:
            continue
        body = inspect.getsource(fn)
        if "get_llm_provider(" not in body:
            continue
        assert ("WRITING)" in body) == (name in writing_routes), name
    assert src.count("get_llm_provider(") >= len(writing_routes)


# ── changing models ───────────────────────────────────────────

def test_changing_the_writing_model_keeps_the_analysis_model(client, db):
    """The PATCH used to assign preferred_model unconditionally — a writing-only
    change would have wiped the analysis model, and every AI feature with it."""
    _key(db, writing=None)
    r = client.patch("/keys/anthropic", json={"writing_model": SONNET})
    assert r.status_code == 200
    key = _reload(db)
    assert key.preferred_model == HAIKU and key.writing_model == SONNET


def test_clearing_the_writing_model_falls_back(client, db):
    _key(db)
    client.patch("/keys/anthropic", json={"writing_model": None})
    assert _reload(db).writing_model is None
    assert client.get("/keys").json()[0]["writing_model"] is None


def test_keys_list_reports_both_models(client, db):
    _key(db)
    row = client.get("/keys").json()[0]
    assert row["preferred_model"] == HAIKU and row["writing_model"] == SONNET


# ── verdicts stay with their model ────────────────────────────

def test_dead_writing_model_does_not_block_scoring(client, db):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, SONNET)
    assert client.get(LLM_URL).json()["last_error_kind"] is None


def test_dead_analysis_model_still_blocks_scoring(client, db):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, HAIKU)
    assert client.get(LLM_URL).json()["last_error_kind"] == "invalid_model"


def test_rejected_key_blocks_scoring_whichever_model_found_it(client, db):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_KEY, SONNET)
    assert _reload(db).last_error_model is None
    assert client.get(LLM_URL).json()["last_error_kind"] == "invalid_key"


def test_pre_split_verdicts_apply_to_every_model(db, test_user):
    """Rows recorded before a key had two models carry no model."""
    key = _key(db)
    key.last_error_kind = models.KEY_ERROR_INVALID_MODEL
    db.commit()
    assert error_applies_to(key, HAIKU) and error_applies_to(key, SONNET)


def test_scoring_success_does_not_erase_a_writing_failure(client, db):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, SONNET)
    client.post(STATUS_URL, json={"kind": None})
    key = _reload(db)
    assert key.last_error_kind == "invalid_model" and key.last_error_model == SONNET


def test_worker_verdicts_are_about_the_analysis_model(client, db):
    _key(db)
    client.post(STATUS_URL, json={"kind": "invalid_model", "detail": "gone"})
    assert _reload(db).last_error_model == HAIKU


def _complete(db, monkeypatch, model, fail=None):
    import litellm
    from app import llm as llm_mod

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]

    def fake(**kw):
        if fail:
            raise fail
        return _Resp()
    monkeypatch.setattr(litellm, "completion", fake)
    try:
        llm_mod.llm_complete(system="s", messages=[{"role": "user", "content": "x"}],
                             api_key="k", model=model, db=db, user_id=TEST_USER_ID)
    except Exception:
        pass


def test_foreground_failure_is_recorded_against_the_model_used(db, test_user, monkeypatch):
    import litellm
    _key(db)
    _complete(db, monkeypatch, SONNET, litellm.BadRequestError(
        message="model claude-sonnet-4-6 not found", model=SONNET, llm_provider="anthropic"))
    key = _reload(db)
    assert key.last_error_kind == "invalid_model" and key.last_error_model == SONNET


def test_retired_model_error_names_the_model(db, test_user, monkeypatch):
    """A key has two models; "the selected model" would not say which to change."""
    import litellm
    from fastapi import HTTPException
    from app import llm as llm_mod
    _key(db)
    monkeypatch.setattr(litellm, "completion", lambda **kw: (_ for _ in ()).throw(
        litellm.BadRequestError(message="model not found", model=SONNET,
                                llm_provider="anthropic")))
    try:
        llm_mod.llm_complete(system="s", messages=[], api_key="k", model=SONNET)
    except HTTPException as e:
        assert SONNET in e.detail
    else:
        raise AssertionError("expected HTTPException")


def test_analysis_success_does_not_clear_a_writing_failure(db, test_user, monkeypatch):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, SONNET)
    _complete(db, monkeypatch, HAIKU)
    assert _reload(db).last_error_kind == "invalid_model"


def test_writing_success_clears_a_writing_failure(db, test_user, monkeypatch):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, SONNET)
    _complete(db, monkeypatch, SONNET)
    assert _reload(db).last_error_kind is None


def test_replacing_the_failed_writing_model_clears_its_verdict(client, db):
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, SONNET)
    client.patch("/keys/anthropic", json={"writing_model": "claude-opus-4-7"})
    assert _reload(db).last_error_kind is None


def test_replacing_the_writing_model_keeps_an_analysis_verdict(client, db):
    """Scoring is still calling the dead analysis model; the banner must stay."""
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, HAIKU)
    client.patch("/keys/anthropic", json={"writing_model": "claude-opus-4-7"})
    key = _reload(db)
    assert key.last_error_kind == "invalid_model" and key.last_error_model == HAIKU


def test_writing_fallback_keeps_a_verdict_alive(client, db):
    """Clearing the writing model makes it fall back to the analysis model — if
    THAT is the dead one, it is still in use and the verdict stays."""
    _key(db)
    _record(db, models.KEY_ERROR_INVALID_MODEL, HAIKU)
    client.patch("/keys/anthropic", json={"writing_model": None})
    assert _reload(db).last_error_kind == "invalid_model"


def test_replacing_the_analysis_model_resets_the_unusable_streak(client, db):
    _key(db)
    key = _reload(db)
    key.unusable_streak = 2
    db.commit()
    client.patch("/keys/anthropic", json={"writing_model": "claude-opus-4-7"})
    assert _reload(db).unusable_streak == 2      # scorer's model unchanged
    client.patch("/keys/anthropic", json={"preferred_model": SONNET})
    assert _reload(db).unusable_streak == 0


# ── the unusable-output count ─────────────────────────────────

def test_scoring_config_reports_the_unusable_count(client, db):
    """The worker needs it to know a success must be reported."""
    key = _key(db)
    key.unusable_streak = 2
    db.commit()
    assert client.get(LLM_URL).json()["unusable_streak"] == 2


def test_count_is_consecutive_end_to_end(client, db):
    _key(db)
    for _ in range(2):
        client.post(STATUS_URL, json={"kind": "unusable_output", "detail": "x"})
    client.post(STATUS_URL, json={"kind": None})
    r = client.post(STATUS_URL, json={"kind": "unusable_output", "detail": "x"})
    assert r.json() == {"status": "counted", "streak": 1}


def test_clearing_an_unusable_verdict_resets_its_count(client, db):
    """Staging, 2026-10-01: a pre-split verdict cleared on a writing-model change
    but left the count at 4, so the next single bad answer would re-raise it."""
    key = _key(db)
    key.last_error_kind = models.KEY_ERROR_UNUSABLE_OUTPUT
    key.unusable_streak = 4
    db.commit()
    client.patch("/keys/anthropic", json={"writing_model": "claude-opus-4-7"})
    key = _reload(db)
    assert key.last_error_kind is None and key.unusable_streak == 0
