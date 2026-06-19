"""Résumé tailoring Phase 1 — structured parse, honesty facts, ingest endpoint,
and stale-on-edit."""

import json

from app import models, resume_tailor, schemas

from .conftest import TEST_USER_ID

# A realistic parse output (what the LLM would return for a 26-year career).
PARSED = {
    "contact": {"name": "Duane Pinkerton", "location": "Oakland, CA", "links": ["github.com/x"]},
    "summary": "20+ years in technical professional services.",
    "skills": [{"label": "Languages", "items": ["Python", "Java", "SQL"]}],
    "experience": [
        {"company": "Responsys / Oracle", "titles": ["Solutions Architect"],
         "start": "2007", "end": "2026",
         "phases": [{"label": "Building", "start": "2007", "end": "2013", "bullets": ["Built pipelines"]}],
         "notable": ["Disney", "Verizon"]},
        {"company": "Extensity", "titles": ["Consultant"], "start": "1999", "end": "2007",
         "bullets": ["Multi-currency support"]},
    ],
    "education": [{"degree": "BA Computer Science", "school": "UC Berkeley"}],
    "projects": [],
}


def _seed_profile(db, resume_text="My résumé text", structured=None, stale=True):
    p = models.Profile(
        user_id=TEST_USER_ID, name="default", is_active=True,
        resume_text=resume_text, resume_structured=structured, resume_structured_stale=stale,
    )
    db.add(p)
    db.commit()
    return p


def _seed_llm_key(db):
    from app.security import encrypt_api_key
    db.add(models.UserAPIKey(
        user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
        encrypted_key=encrypt_api_key("sk-test"),
    ))
    db.commit()


# ── parse ─────────────────────────────────────────────────────

def test_parse_resume_text(monkeypatch):
    monkeypatch.setattr(resume_tailor, "llm_complete", lambda **k: json.dumps(PARSED))
    out = resume_tailor.parse_resume_text("text", "key", "model")
    assert out.contact.name == "Duane Pinkerton"
    assert out.experience[0].phases[0].label == "Building"
    assert out.experience[1].company == "Extensity"


def test_parse_strips_code_fences(monkeypatch):
    fenced = "```json\n" + json.dumps(PARSED) + "\n```"
    monkeypatch.setattr(resume_tailor, "llm_complete", lambda **k: fenced)
    assert resume_tailor.parse_resume_text("text", "key", "model").summary.startswith("20+")


def test_parse_empty_text_400():
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        resume_tailor.parse_resume_text("   ", "key", "model")
    assert e.value.status_code == 400


def test_parse_malformed_json_502(monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(resume_tailor, "llm_complete", lambda **k: "not json")
    with pytest.raises(HTTPException) as e:
        resume_tailor.parse_resume_text("text", "key", "model")
    assert e.value.status_code == 502


# ── honesty facts ─────────────────────────────────────────────

def test_honesty_facts_total_years():
    structured = schemas.ResumeStructured.model_validate(PARSED)
    facts = resume_tailor.derive_honesty_facts(structured)
    assert facts["earliest_start_year"] == 1999
    assert facts["latest_end_year"] == 2026
    assert facts["total_years_experience"] == 27       # 2026 - 1999
    assert {e["company"] for e in facts["employers"]} == {"Responsys / Oracle", "Extensity"}


def test_honesty_facts_present_means_now():
    data = {"experience": [{"company": "X", "start": "2010", "end": "present"}]}
    structured = schemas.ResumeStructured.model_validate(data)
    import datetime as dt
    facts = resume_tailor.derive_honesty_facts(structured)
    assert facts["latest_end_year"] == dt.date.today().year


def test_honesty_facts_empty_resume():
    facts = resume_tailor.derive_honesty_facts(schemas.ResumeStructured())
    assert facts["total_years_experience"] is None
    assert facts["employers"] == []


# ── ingest endpoint ───────────────────────────────────────────

def test_ingest_endpoint(client, db, monkeypatch):
    _seed_profile(db, stale=True)
    _seed_llm_key(db)
    monkeypatch.setattr(resume_tailor, "llm_complete", lambda **k: json.dumps(PARSED))

    r = client.post("/profile/resume/ingest")
    assert r.status_code == 200
    body = r.json()
    assert body["structured"]["contact"]["name"] == "Duane Pinkerton"
    assert body["honesty_facts"]["total_years_experience"] == 27
    assert body["stale"] is False

    # Persisted + stale cleared
    prof = client.get("/profile").json()
    assert prof["resume_structured"]["summary"].startswith("20+")
    assert prof["resume_structured_stale"] is False


def test_ingest_no_profile_404(client):
    r = client.post("/profile/resume/ingest")
    assert r.status_code == 404


def test_ingest_no_llm_key_400(client, db):
    _seed_profile(db)
    r = client.post("/profile/resume/ingest")
    assert r.status_code == 400        # get_llm_provider raises when no key


# ── stale-on-edit ─────────────────────────────────────────────

def test_saving_new_resume_text_marks_stale(client, db):
    _seed_profile(db, resume_text="old", structured={"summary": "cached"}, stale=False)
    r = client.put("/profile", json={"resume_text": "brand new résumé"})
    assert r.status_code == 200
    assert r.json()["resume_structured_stale"] is True


def test_saving_same_resume_text_keeps_fresh(client, db):
    _seed_profile(db, resume_text="same", structured={"summary": "cached"}, stale=False)
    r = client.put("/profile", json={"resume_text": "same", "full_name": "Duane"})
    assert r.status_code == 200
    assert r.json()["resume_structured_stale"] is False
