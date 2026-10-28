"""Company research reaches the employer-facing generators — application answers,
refine and interview prep — when the job has it, and changes nothing when not."""

from uuid import UUID

from app import models
from app.routers import generate as generate_mod

from .test_writing_skills import _capture, _scrape, _seed

RESEARCH = "Globex makes logistics software and is expanding into Europe."
REFINE = {"template_idx": 0, "current_answer": "Draft.", "messages": [{"role": "user", "content": "Shorter."}]}


def _all_prompts(client, monkeypatch, rid):
    """Every generator's full prompt (system + user), keyed by name."""
    out = {}
    for name, call in (
        ("application", lambda: client.post(f"/jobs/{rid}/application/0")),
        ("refine", lambda: client.post(f"/jobs/{rid}/refine", json=REFINE)),
        ("interview_prep", lambda: client.post(f"/jobs/{rid}/interview-prep")),
    ):
        seen = _capture(monkeypatch)
        call()  # interview prep rejects the fake's empty answer; the prompt is what's checked
        assert "system" in seen, name
        out[name] = seen["system"] + "\n" + seen["user"]
    return out


def _set_research(db, rid, text):
    review = db.get(models.UserJobReview, UUID(rid))
    review.research_summary = text
    db.commit()


def test_research_reaches_every_employer_facing_generator(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    _set_research(db, rid, RESEARCH)
    for name, prompt in _all_prompts(client, monkeypatch, rid).items():
        assert "## Company Research (background)" in prompt, name
        assert RESEARCH in prompt, name
        # After the posting it describes, before the résumé.
        assert prompt.index("## Job Posting") < prompt.index(RESEARCH) < prompt.index("## Candidate Resume"), name


def test_no_research_means_no_section(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    for blank in (None, "   "):
        _set_research(db, rid, blank)
        for name, prompt in _all_prompts(client, monkeypatch, rid).items():
            assert "Company Research" not in prompt, (name, blank)


def test_oversized_research_is_capped():
    class _Review:
        research_summary = "x" * (generate_mod.MAX_RESEARCH_CONTEXT + 5000)
    block = generate_mod._research_block(_Review())
    assert block.endswith("…(truncated)")
    assert block.count("x") == generate_mod.MAX_RESEARCH_CONTEXT
