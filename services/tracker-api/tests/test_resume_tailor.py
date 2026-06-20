"""Résumé tailoring Phase 2a — deterministic diff, classification, and the tailor /
refine / decisions endpoints (LLM mocked)."""

from unittest.mock import patch

from app import models, resume_tailor, schemas

from .conftest import TEST_USER_ID

ORIGINAL = {
    "summary": "Built data pipelines.",
    "skills": [{"label": "Lang", "items": ["Python", "Java"]}],
    "experience": [{
        "company": "Acme", "titles": ["Engineer"], "start": "2010", "end": "2020",
        "bullets": ["Built ETL pipelines", "Led migration"],
    }],
    "education": [{"degree": "BA CS", "school": "UCB"}],
    "projects": [],
}

# Reworded summary + skill item + first bullet; titles/dates untouched → all wording.
TAILORED = {
    "summary": "Built scalable data pipelines.",
    "skills": [{"label": "Lang", "items": ["Python", "JavaScript"]}],
    "experience": [{
        "company": "Acme", "titles": ["Engineer"], "start": "2010", "end": "2020",
        "bullets": ["Engineered ETL data pipelines", "Led migration"],
    }],
    "education": [{"degree": "BA CS", "school": "UCB"}],
    "projects": [],
}

NOTES = [{"before": "Built ETL pipelines", "after": "Engineered ETL data pipelines",
          "type": "vocabulary", "rationale": "match the JD wording",
          "trigger": "experience with ETL data pipelines"}]

JOB = {"title": "Data Eng", "company": "Globex", "url": "https://x/1", "source": "manual"}


def _s(d):
    return schemas.ResumeStructured.model_validate(d)


# ── pure diff / classify ──────────────────────────────────────

def test_diff_finds_changed_leaves():
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(TAILORED), NOTES)
    paths = {c["path"] for c in changes}
    assert paths == {"summary", "skills/0/items", "experience/0/bullets/0"}
    assert all(c["type"] != "factual" for c in changes)        # none touch a factual anchor
    bullet = next(c for c in changes if c["path"] == "experience/0/bullets/0")
    assert bullet["before"] == "Built ETL pipelines"
    assert bullet["after"] == "Engineered ETL data pipelines"
    assert bullet["type"] == "vocabulary"                      # from the matched note
    assert bullet["rationale"] == "match the JD wording"       # note matched by before-text
    assert bullet["trigger"] == "experience with ETL data pipelines"   # job-posting phrase


def test_diff_identical_is_empty():
    assert resume_tailor.diff_structured(_s(ORIGINAL), _s(ORIGINAL)) == []


def test_title_and_date_changes_are_factual():
    t = {**ORIGINAL, "experience": [{**ORIGINAL["experience"][0],
                                     "titles": ["Senior Engineer"], "end": "2021"}]}
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(t))
    by_path = {c["path"]: c for c in changes}
    assert by_path["experience/0/titles"]["type"] == "factual"
    assert by_path["experience/0/end"]["type"] == "factual"


def test_tailor_tolerates_surrounding_text(monkeypatch):
    """Models (esp. Haiku on refine) sometimes wrap the JSON in prose / fences /
    a trailing note. Parsing must extract the first object and ignore the rest."""
    import json as _json
    payload = _json.dumps({"tailored": TAILORED, "notes": NOTES})
    monkeypatch.setattr(
        resume_tailor, "llm_complete",
        lambda **k: "Sure, here you go:\n```json\n" + payload + "\n```\nHope that helps!",
    )
    tailored, notes = resume_tailor.tailor_resume(
        _s(ORIGINAL), {"total_years_experience": 10}, "job", "style", "k", "m")
    assert tailored.summary == "Built scalable data pipelines."
    assert notes == NOTES


def test_build_state_counts_flagged():
    t = {**ORIGINAL, "experience": [{**ORIGINAL["experience"][0], "titles": ["Senior Engineer"]}]}
    state = resume_tailor.build_tailor_state(_s(ORIGINAL), _s(t), [], "model", {"total_years_experience": 10})
    assert state["status"] == "draft"
    assert state["flagged_count"] == 1
    assert state["total_years"] == 10


# ── endpoints ─────────────────────────────────────────────────

def _seed(db, *, stale=False):
    db.add(models.Profile(
        user_id=TEST_USER_ID, name="default", is_active=True,
        resume_text="My résumé",
        resume_structured=_s(ORIGINAL).model_dump(),   # what ingest actually stores
        resume_structured_stale=stale,
    ))
    from app.security import encrypt_api_key
    db.add(models.UserAPIKey(
        user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
        encrypted_key=encrypt_api_key("sk-test"),
    ))
    db.commit()


def _scrape(client):
    with patch("app.routers.jobs._celery"):
        client.post(f"/jobs?user_id={TEST_USER_ID}", json=JOB)
    return client.get("/jobs").json()["items"][0]["id"]


def test_tailor_endpoint(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))

    r = client.post(f"/jobs/{rid}/tailor-resume")
    assert r.status_code == 200
    state = r.json()
    assert len(state["changes"]) == 3
    assert state["status"] == "draft"
    assert state["original"]["summary"] == "Built data pipelines."
    assert state["tailored"]["summary"] == "Built scalable data pipelines."

    # Persisted + fetchable
    got = client.get(f"/jobs/{rid}/tailor-resume").json()
    assert len(got["changes"]) == 3
    assert got["base_changed"] is False


def test_tailor_requires_resume(client, db):
    # profile exists but no résumé text
    db.add(models.Profile(user_id=TEST_USER_ID, name="default", is_active=True, resume_text=""))
    db.commit()
    rid = _scrape(client)
    assert client.post(f"/jobs/{rid}/tailor-resume").status_code == 400


def test_tailor_reparses_when_stale(client, db, monkeypatch):
    _seed(db, stale=True)
    rid = _scrape(client)
    calls = {"parse": 0}

    def fake_parse(text, key, model):
        calls["parse"] += 1
        return _s(ORIGINAL)
    monkeypatch.setattr(resume_tailor, "parse_resume_text", fake_parse)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))

    assert client.post(f"/jobs/{rid}/tailor-resume").status_code == 200
    assert calls["parse"] == 1                       # re-ingested because stale
    assert client.get("/profile").json()["resume_structured_stale"] is False


def test_get_before_tailor_404(client, db):
    _seed(db)
    rid = _scrape(client)
    assert client.get(f"/jobs/{rid}/tailor-resume").status_code == 404


def test_decisions_accept_reject(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))
    state = client.post(f"/jobs/{rid}/tailor-resume").json()
    cid = state["changes"][0]["id"]

    r = client.patch(f"/jobs/{rid}/tailor-resume/decisions", json={"decisions": {cid: "rejected"}})
    assert r.status_code == 200
    updated = {c["id"]: c["decision"] for c in r.json()["changes"]}
    assert updated[cid] == "rejected"
    # persisted
    assert {c["id"]: c["decision"] for c in client.get(f"/jobs/{rid}/tailor-resume").json()["changes"]}[cid] == "rejected"


def test_decisions_reject_invalid_value(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))
    cid = client.post(f"/jobs/{rid}/tailor-resume").json()["changes"][0]["id"]
    assert client.patch(f"/jobs/{rid}/tailor-resume/decisions",
                        json={"decisions": {cid: "maybe"}}).status_code == 400


def test_refine_carries_decisions(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))
    state = client.post(f"/jobs/{rid}/tailor-resume").json()
    cid = state["changes"][0]["id"]
    client.patch(f"/jobs/{rid}/tailor-resume/decisions", json={"decisions": {cid: "accepted"}})

    # Refine returns the same tailored (so same change ids) — decision must persist.
    r = client.post(f"/jobs/{rid}/tailor-resume/refine", json={"instruction": "punchier"})
    assert r.status_code == 200
    carried = {c["id"]: c["decision"] for c in r.json()["changes"]}
    assert carried[cid] == "accepted"


def test_refine_before_tailor_404(client, db):
    _seed(db)
    rid = _scrape(client)
    assert client.post(f"/jobs/{rid}/tailor-resume/refine", json={"instruction": "x"}).status_code == 404
