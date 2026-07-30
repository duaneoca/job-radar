"""User-loadable writing skills: schema caps, the scope→prompt injection map, and
the rule that a skill never reaches a meta or extraction prompt."""

from unittest.mock import patch

from app import models, resume_tailor, schemas
from app.routers import generate as generate_mod

from .conftest import TEST_USER_ID

JOB = {"title": "Data Eng", "company": "Globex", "url": "https://x/1", "source": "manual",
       "description": "Build pipelines."}

SKILL_TEXT = "Never open with 'In today's fast-paced world'. Prefer short concrete sentences."


def _skill(**kw):
    base = {"id": "s1", "name": "Humanizer", "content": SKILL_TEXT, "enabled": True,
            "scopes": list(schemas.DEFAULT_SKILL_SCOPES)}
    base.update(kw)
    return base


def _seed(db, skills=None):
    db.add(models.Profile(user_id=TEST_USER_ID, name="default", is_active=True,
                          resume_text="I build pipelines."))
    db.add(models.Criteria(user_id=TEST_USER_ID, name="default", is_active=True,
                           job_titles=["Data Eng"], writing_skills=skills))
    from app.security import encrypt_api_key
    db.add(models.UserAPIKey(user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
                             encrypted_key=encrypt_api_key("sk-test")))
    db.commit()


def _scrape(client):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    return client.get("/jobs").json()["items"][0]["id"]


class _Crit:
    """Stand-in for a Criteria row — skills_block only reads .writing_skills."""
    def __init__(self, skills):
        self.writing_skills = skills


# ── skills_block unit behaviour ───────────────────────────────

def test_block_empty_when_no_skills():
    assert generate_mod.skills_block(_Crit(None), "application") == ""
    assert generate_mod.skills_block(_Crit([]), "application") == ""


def test_block_filters_disabled_and_out_of_scope():
    skills = [
        _skill(id="a", name="Off", enabled=False),
        _skill(id="b", name="ResumeOnly", scopes=["resume"]),
        _skill(id="c", name="AppOnly", scopes=["application"]),
    ]
    block = generate_mod.skills_block(_Crit(skills), "application")
    assert "AppOnly" in block
    assert "Off" not in block and "ResumeOnly" not in block


def test_block_preserves_order_and_adds_subordination_footer():
    skills = [_skill(id="a", name="First"), _skill(id="b", name="Second")]
    block = generate_mod.skills_block(_Crit(skills), "application")
    assert block.index("First") < block.index("Second")
    assert "WORDING ONLY" in block
    assert "valid JSON" not in block            # non-strict scope


def test_block_strict_adds_output_format_guard():
    block = generate_mod.skills_block(_Crit([_skill()]), "interview_prep", strict_output=True)
    assert "must not change the required output format" in block


def test_block_truncates_at_budget():
    big = _skill(id="big", name="Big", content="x" * (generate_mod.SKILLS_CHAR_BUDGET + 10))
    after = _skill(id="after", name="After")
    block = generate_mod.skills_block(_Crit([big, after]), "application")
    assert "…(truncated)" in block                       # kept, not silently dropped
    import re
    longest = max(len(m) for m in re.findall(r"x+", block))
    assert longest <= generate_mod.SKILLS_CHAR_BUDGET     # content capped at the budget
    assert "After" not in block                          # budget already spent


def test_block_tolerates_garbage_entries():
    assert generate_mod.skills_block(_Crit(["nonsense", None, 5]), "application") == ""


# ── schema caps ───────────────────────────────────────────────

def test_unknown_scope_rejected(client, db):
    _seed(db)
    r = client.put("/criteria", json={"job_titles": ["X"],
                                      "writing_skills": [_skill(scopes=["nope"])]})
    assert r.status_code == 422


def test_oversized_skill_rejected(client, db):
    _seed(db)
    huge = _skill(content="x" * (schemas.MAX_SKILL_CONTENT + 1))
    assert client.put("/criteria", json={"job_titles": ["X"], "writing_skills": [huge]}).status_code == 422


def test_total_budget_rejected(client, db):
    _seed(db)
    chunk = "x" * schemas.MAX_SKILL_CONTENT
    many = [_skill(id=str(i), content=chunk) for i in range(4)]     # 80k > 60k cap
    assert client.put("/criteria", json={"job_titles": ["X"], "writing_skills": many}).status_code == 422


def test_round_trip(client, db):
    _seed(db)
    r = client.put("/criteria", json={"job_titles": ["X"], "writing_skills": [_skill()]})
    assert r.status_code == 200
    got = client.get("/criteria").json()["writing_skills"]
    assert got[0]["name"] == "Humanizer" and got[0]["content"] == SKILL_TEXT


def test_skill_defaults_exclude_scoring(client, db):
    """Scoring has a hard JSON contract, so it is opt-in rather than default."""
    _seed(db)
    client.put("/criteria", json={"job_titles": ["X"],
                                  "writing_skills": [{"id": "s", "name": "n", "content": "c"}]})
    assert client.get("/criteria").json()["writing_skills"][0]["scopes"] == \
        list(schemas.DEFAULT_SKILL_SCOPES)


# ── injection into real prompts ───────────────────────────────

def _capture(monkeypatch):
    seen = {}

    def fake(system, messages, api_key, model, max_tokens=1024):
        seen["system"] = system
        seen["user"] = messages[-1]["content"]
        return '{"questions": []}'
    monkeypatch.setattr(generate_mod, "llm_complete", fake)
    return seen


def test_skill_reaches_research_and_application(client, db, monkeypatch):
    _seed(db, [_skill()])
    rid = _scrape(client)

    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/research")
    assert SKILL_TEXT in seen["system"]

    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/application/0")
    assert SKILL_TEXT in seen["system"]


def test_skill_reaches_interview_prep_with_json_guard(client, db, monkeypatch):
    _seed(db, [_skill()])
    rid = _scrape(client)
    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/interview-prep")
    assert SKILL_TEXT in seen["system"]
    assert "must not change the required output format" in seen["system"]


def test_skill_reaches_resume_tailor_below_the_contract(client, db, monkeypatch):
    """The skill must sit under the honesty contract, never above it."""
    _seed(db, [_skill()])
    rid = _scrape(client)
    seen = {}

    def fake_tailor(structured, honesty, job_text, style, api_key, model, *, extra=None, skills_text=""):
        seen["skills_text"] = skills_text
        seen["user"] = resume_tailor._tailor_messages(
            structured, honesty, job_text, style, extra, skills_text)
        return structured, []
    monkeypatch.setattr(resume_tailor, "tailor_resume", fake_tailor)
    monkeypatch.setattr(resume_tailor, "parse_resume_text",
                        lambda *a, **k: schemas.ResumeStructured.model_validate(
                            {"summary": "s", "skills": [], "experience": [],
                             "education": [], "projects": []}))

    client.post(f"/jobs/{rid}/tailor-resume")
    assert SKILL_TEXT in seen["skills_text"]
    assert seen["user"].index("HONESTY CONTRACT") < seen["user"].index(SKILL_TEXT)


def test_skill_never_reaches_meta_or_extraction_prompts(client, db, monkeypatch):
    """extract-changes writes prompts, and the résumé parser extracts data — a
    style skill in either would corrupt their output."""
    _seed(db, [_skill(scopes=list(schemas.SKILL_SCOPES))])   # every scope on
    rid = _scrape(client)

    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/extract-changes",
                json={"change_type": "voice", "current_content": "",
                      "messages": [{"role": "user", "content": "hi"}]})
    assert SKILL_TEXT not in seen["system"]

    # The résumé parser is structurally immune: it never receives criteria, so no
    # skill can reach it. Pin that, since giving it one would corrupt extraction.
    import inspect
    assert "criteria" not in inspect.signature(resume_tailor.parse_resume_text).parameters
    assert "skills" not in inspect.signature(resume_tailor.parse_resume_text).parameters


def test_disabled_skill_is_not_injected(client, db, monkeypatch):
    _seed(db, [_skill(enabled=False)])
    rid = _scrape(client)
    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/research")
    assert SKILL_TEXT not in seen["system"]


def test_scope_narrowing_is_respected(client, db, monkeypatch):
    """A research-only skill must not leak into application materials."""
    _seed(db, [_skill(scopes=["research"])])
    rid = _scrape(client)

    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/research")
    assert SKILL_TEXT in seen["system"]

    seen = _capture(monkeypatch)
    client.post(f"/jobs/{rid}/application/0")
    assert SKILL_TEXT not in seen["system"]
