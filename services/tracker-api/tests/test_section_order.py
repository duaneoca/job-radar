"""Résumé section order — one value that every renderer follows.

The bug this exists for: the tailor page, the Classic PDF and the Modern PDF
each hardcoded their own section order, and the parsed résumé didn't record its
own, so the screen and the PDF disagreed and neither matched the résumé. Section
order now lives on the structured résumé; per job it is one "section order"
change that a refine or the arrows can produce and accept/reject can undo.
"""

from uuid import UUID

import pytest

from app import models, resume_tailor, schemas

from .conftest import TEST_USER_ID
from .test_resume_tailor import ORIGINAL, TAILORED, _s, _scrape

AS_WRITTEN = ["summary", "skills", "projects", "experience", "education"]
EXP_FIRST = ["summary", "skills", "experience", "education", "projects"]

RESUME_TEXT = """Jane Doe
jane@x.com · Toronto

Summary
Builds data platforms.

Skills
Python, SQL

Projects
Job Radar — job hunting tool.

Experience
Acme — Engineer, 2010–2020

Education
BA CS, UCB
"""


# ── reading the order from the résumé text ────────────────────

def test_detects_the_order_the_headings_appear_in():
    assert resume_tailor.detect_section_order(RESUME_TEXT) == AS_WRITTEN


def test_heading_spellings_and_an_unheaded_summary():
    text = "Jane\nA summary paragraph with no heading.\nPROFESSIONAL EXPERIENCE\nAcme\n" \
           "Technical Skills:\nPython\nEducation & Training\nBA\n"
    # Summary is the unheaded opening; projects never appear, so they go last.
    assert resume_tailor.detect_section_order(text) == \
        ["summary", "experience", "skills", "education", "projects"]


def test_prose_mentioning_a_section_is_not_a_heading():
    text = "Ten years of experience building data platforms for clients.\nSkills\nPython"
    assert resume_tailor.detect_section_order(text) is None    # one heading says nothing


@pytest.mark.parametrize("text", [None, "", "just a name"])
def test_nothing_to_go_on(text):
    assert resume_tailor.detect_section_order(text) is None


def test_normalize_completes_and_cleans():
    assert schemas.normalize_section_order(["experience", "bogus", "experience", "summary"]) == \
        ["experience", "summary", "skills", "projects", "education"]
    assert schemas.normalize_section_order("experience") is None
    assert schemas.normalize_section_order(["bogus"]) is None


def test_bad_order_never_fails_the_resume():
    assert _s({**ORIGINAL, "section_order": "garbage"}).section_order is None


def test_parse_records_the_order_from_the_text(monkeypatch):
    import json
    monkeypatch.setattr(resume_tailor, "llm_complete", lambda **k: json.dumps(ORIGINAL))
    parsed = resume_tailor.parse_resume_text(RESUME_TEXT, "k", "m")
    assert parsed.section_order == AS_WRITTEN


# ── the tailor never moves sections on its own ────────────────

def _model_returns(monkeypatch, resume: dict):
    import json
    monkeypatch.setattr(resume_tailor, "llm_complete",
                        lambda **k: json.dumps({"tailored": resume, "notes": []}))


def test_first_tailor_keeps_the_original_order(monkeypatch):
    _model_returns(monkeypatch, {**TAILORED, "section_order": EXP_FIRST})
    tailored, _ = resume_tailor.tailor_resume(
        _s({**ORIGINAL, "section_order": AS_WRITTEN}), {}, "job", "style", "k", "m")
    assert tailored.section_order == AS_WRITTEN


def test_refine_may_move_sections(monkeypatch):
    _model_returns(monkeypatch, {**TAILORED, "section_order": EXP_FIRST})
    tailored, _ = resume_tailor.tailor_resume(
        _s({**ORIGINAL, "section_order": AS_WRITTEN}), {}, "job", "style", "k", "m",
        extra="put experience before projects")
    assert tailored.section_order == EXP_FIRST


def test_refine_that_omits_the_order_keeps_it(monkeypatch):
    _model_returns(monkeypatch, TAILORED)
    tailored, _ = resume_tailor.tailor_resume(
        _s({**ORIGINAL, "section_order": EXP_FIRST}), {}, "job", "style", "k", "m", extra="punchier")
    assert tailored.section_order == EXP_FIRST


def test_contract_names_the_one_exception():
    assert "explicitly asks to move whole sections" in resume_tailor.HONESTY_CORE


# ── diff and the effective résumé ─────────────────────────────

def _state(orig_order, tail_order, decision="pending"):
    st = resume_tailor.build_tailor_state(
        _s({**ORIGINAL, "section_order": orig_order}),
        _s({**ORIGINAL, "section_order": tail_order}), [], "m", {})
    for c in st["changes"]:
        c["decision"] = decision
    return st


def test_a_section_move_is_one_reorder_change():
    st = _state(AS_WRITTEN, EXP_FIRST)
    (c,) = st["changes"]
    assert (c["path"], c["kind"], c["type"]) == ("section_order", "reordered", "reorder")
    assert c["after_items"] == ["Summary", "Skills", "Experience", "Education", "Projects"]
    assert c["order"] == [0, 1, 3, 4, 2]
    assert st["reorder_count"] == 1


def test_same_order_no_change():
    assert _state(AS_WRITTEN, AS_WRITTEN)["changes"] == []


@pytest.mark.parametrize("decision,expected", [
    ("pending", EXP_FIRST), ("accepted", EXP_FIRST), ("rejected", AS_WRITTEN),
])
def test_effective_order_follows_the_decision(decision, expected):
    assert resume_tailor.effective_resume(_state(AS_WRITTEN, EXP_FIRST, decision))["section_order"] == expected


def test_rejected_move_alongside_bullet_changes():
    """The content-aligned path, not just the legacy one, must restore the order."""
    st = resume_tailor.build_tailor_state(
        _s({**ORIGINAL, "section_order": AS_WRITTEN}),
        _s({**TAILORED, "section_order": EXP_FIRST}), [], "m", {})
    for c in st["changes"]:
        c["decision"] = "rejected" if c["path"] == "section_order" else "accepted"
    eff = resume_tailor.effective_resume(st)
    assert eff["section_order"] == AS_WRITTEN
    assert eff["experience"][0]["bullets"][0] == "Engineered ETL data pipelines"


# ── endpoints ─────────────────────────────────────────────────

def _seed(db, text=RESUME_TEXT):
    from app.security import encrypt_api_key
    db.add(models.Profile(
        user_id=TEST_USER_ID, name="default", is_active=True, resume_text=text,
        resume_structured=_s(ORIGINAL).model_dump(),   # parsed before order existed
        resume_structured_stale=False,
    ))
    db.add(models.UserAPIKey(user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
                             encrypted_key=encrypt_api_key("sk-test"),
                             preferred_model="claude-haiku-4-5"))
    db.commit()


def _tailored(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda s, *a, **k: (
        _s({**TAILORED, "section_order": s.section_order}), []))
    client.post(f"/jobs/{rid}/tailor-resume")
    return rid


def test_tailoring_starts_from_the_resumes_own_order(client, db, monkeypatch):
    """A résumé parsed before order existed gets it from its text — no model call."""
    rid = _tailored(client, db, monkeypatch)
    st = client.get(f"/jobs/{rid}/tailor-resume").json()
    assert st["original"]["section_order"] == AS_WRITTEN
    assert st["effective"]["section_order"] == AS_WRITTEN


def test_get_backfills_a_state_saved_without_an_order(client, db, monkeypatch):
    rid = _tailored(client, db, monkeypatch)
    review = db.query(models.UserJobReview).filter_by(id=UUID(rid)).one()
    old = dict(review.resume_tailor)
    old["original"] = {**old["original"], "section_order": None}
    old["tailored"] = {**old["tailored"], "section_order": None}
    review.resume_tailor = old
    db.commit()

    st = client.get(f"/jobs/{rid}/tailor-resume").json()
    assert st["original"]["section_order"] == AS_WRITTEN
    assert st["tailored"]["section_order"] == AS_WRITTEN
    db.refresh(review)
    assert review.resume_tailor["original"]["section_order"] == AS_WRITTEN   # persisted


def test_arrows_set_the_order_and_accept_it(client, db, monkeypatch):
    rid = _tailored(client, db, monkeypatch)
    r = client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": EXP_FIRST})
    assert r.status_code == 200, r.text
    st = r.json()
    (c,) = [c for c in st["changes"] if c["path"] == "section_order"]
    assert c["decision"] == "accepted"
    assert st["effective"]["section_order"] == EXP_FIRST
    assert st["reorder_count"] == 1


def test_arrows_back_to_the_original_remove_the_change(client, db, monkeypatch):
    rid = _tailored(client, db, monkeypatch)
    client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": EXP_FIRST})
    st = client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": AS_WRITTEN}).json()
    assert not [c for c in st["changes"] if c["path"] == "section_order"]
    assert st["effective"]["section_order"] == AS_WRITTEN


@pytest.mark.parametrize("order", [
    ["summary", "skills", "experience", "education"],                    # one missing
    ["summary", "skills", "experience", "education", "bogus"],           # unknown
    ["summary", "summary", "skills", "experience", "education"],         # repeated
])
def test_arrows_reject_anything_but_a_full_permutation(client, db, monkeypatch, order):
    rid = _tailored(client, db, monkeypatch)
    assert client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": order}).status_code == 400


def test_refine_after_a_rejected_move_names_the_order_to_keep(client, db, monkeypatch):
    rid = _tailored(client, db, monkeypatch)
    st = client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": EXP_FIRST}).json()
    cid = next(c["id"] for c in st["changes"] if c["path"] == "section_order")
    client.patch(f"/jobs/{rid}/tailor-resume/decisions", json={"decisions": {cid: "rejected"}})

    seen = {}

    def fake(current, *a, extra=None, **k):
        seen["extra"], seen["order"] = extra, current.section_order
        return _s({**TAILORED, "section_order": current.section_order}), []
    monkeypatch.setattr(resume_tailor, "tailor_resume", fake)
    client.post(f"/jobs/{rid}/tailor-resume/refine", json={"instruction": "punchier"})
    assert "rejected moving the sections" in seen["extra"]
    assert "summary, skills, projects, experience, education" in seen["extra"]
    assert seen["order"] == AS_WRITTEN                 # not the rejected draft
    assert "section_order" not in seen["extra"].split("bullets in these sections")[-1].split("\n")[0]


def test_a_new_move_is_not_swallowed_by_an_old_rejection(client, db, monkeypatch):
    """One fixed id covers every order; the decision carries only if the order is the same."""
    rid = _tailored(client, db, monkeypatch)
    st = client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": EXP_FIRST}).json()
    cid = next(c["id"] for c in st["changes"] if c["path"] == "section_order")
    client.patch(f"/jobs/{rid}/tailor-resume/decisions", json={"decisions": {cid: "rejected"}})

    asked = ["summary", "experience", "skills", "projects", "education"]
    monkeypatch.setattr(resume_tailor, "tailor_resume",
                        lambda *a, **k: (_s({**TAILORED, "section_order": asked}), []))
    st = client.post(f"/jobs/{rid}/tailor-resume/refine",
                     json={"instruction": "put experience right after the summary"}).json()
    (c,) = [c for c in st["changes"] if c["path"] == "section_order"]
    assert c["decision"] == "pending"
    assert st["effective"]["section_order"] == asked
