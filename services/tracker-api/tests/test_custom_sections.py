"""Custom sections and the résumé's own headings.

Before this, the parsed résumé had five typed slots and nowhere else: unknown
fields were discarded, so a Certifications or "Current stuff" section silently
vanished from every tailored PDF, and headings were replaced with standard
labels. Types stay where behaviour depends on them (honesty facts, factual
flags, layout); names and coverage no longer do.
"""

import json

import pytest

from app import resume_tailor

from .test_resume_tailor import ORIGINAL, _s, _scrape
from .test_section_order import _seed

CERTS = {"title": "Certifications", "entries": [
    {"title": "AWS Solutions Architect (2021)", "bullets": ["Professional level"]},
]}
VOLUNTEER = {"title": "Volunteering", "entries": [
    {"title": "Code Club", "bullets": ["Taught Python to teens"]},
]}
WITH_CUSTOM = {**ORIGINAL,
               "projects": [{"title": "Job Radar", "bullets": ["Built it"]}],
               "custom_sections": [CERTS, VOLUNTEER],
               "section_titles": {"projects": "Current Stuff"}}

TEXT = """Jane Doe

Summary
Builds data platforms.

Current Stuff
Job Radar

Experience
Acme

Certifications
AWS Solutions Architect (2021)

Skills
Python

Education
BA

Volunteering
Code Club
"""


# ── schema ────────────────────────────────────────────────────

def test_custom_sections_get_stable_unique_ids():
    r = _s({**ORIGINAL, "custom_sections": [CERTS, {**CERTS, "entries": []}, VOLUNTEER]})
    assert r.custom_keys() == ["custom:certifications", "custom:certifications-2", "custom:volunteering"]


def test_an_id_already_assigned_is_kept():
    """Identity has to survive a tailor round-trip even if the title changes."""
    r = _s({**ORIGINAL, "custom_sections": [{**CERTS, "id": "custom:certs-old"}]})
    assert r.custom_keys() == ["custom:certs-old"]


def test_headings_are_cleaned():
    r = _s({**ORIGINAL, "section_titles": {"projects": "  Current Stuff ", "bogus": "x",
                                           "skills": "", "summary": "y" * 200}})
    assert r.section_titles == {"projects": "Current Stuff", "summary": "y" * 80}


def test_order_covers_custom_sections():
    r = _s({**WITH_CUSTOM, "section_order": ["custom:volunteering", "experience", "custom:nope"]})
    # Unknown custom key dropped; typed sections appended in default order, then
    # the custom section the order didn't mention.
    assert r.section_order == ["custom:volunteering", "experience", "summary", "skills",
                               "projects", "education", "custom:certifications"]


def test_resumes_without_custom_sections_are_unchanged():
    assert _s(ORIGINAL).custom_sections == [] and _s(ORIGINAL).section_titles == {}


# ── parsing and order ─────────────────────────────────────────

def test_order_is_read_using_the_resumes_own_headings():
    order = resume_tailor.detect_section_order(TEXT, _s(WITH_CUSTOM))
    assert order == ["summary", "projects", "experience", "custom:certifications",
                     "skills", "education", "custom:volunteering"]


def test_parse_keeps_every_section_and_its_heading(monkeypatch):
    monkeypatch.setattr(resume_tailor, "llm_complete", lambda **k: json.dumps(WITH_CUSTOM))
    r = resume_tailor.parse_resume_text(TEXT, "k", "m")
    assert [s.title for s in r.custom_sections] == ["Certifications", "Volunteering"]
    assert r.section_titles == {"projects": "Current Stuff"}
    assert r.section_order[1] == "projects" and "custom:volunteering" in r.section_order


def test_parse_prompt_forbids_dropping_sections():
    assert "NEVER drop a section" in resume_tailor.DEFAULT_RESUME_PARSE_PROMPT


# ── tailoring keeps sections intact ───────────────────────────

def test_tailor_cannot_rename_drop_or_invent_sections(monkeypatch):
    returned = {**WITH_CUSTOM,
                "section_titles": {"projects": "Projects"},                    # renamed
                "custom_sections": [
                    {"title": "Certs", "id": "custom:certifications",          # retitled
                     "entries": [{"title": "AWS Solutions Architect (2021)",
                                  "bullets": ["Professional-level certification"]}]},
                    {"title": "Hobbies", "entries": [{"title": "Chess", "bullets": []}]},  # invented
                ]}                                                              # Volunteering dropped
    monkeypatch.setattr(resume_tailor, "llm_complete",
                        lambda **k: json.dumps({"tailored": returned, "notes": []}))
    t, _ = resume_tailor.tailor_resume(_s(WITH_CUSTOM), {}, "job", "style", "k", "m")
    assert t.section_titles == {"projects": "Current Stuff"}
    assert [(s.id, s.title) for s in t.custom_sections] == [
        ("custom:certifications", "Certifications"), ("custom:volunteering", "Volunteering")]
    assert t.custom_sections[0].entries[0].bullets == ["Professional-level certification"]  # edit kept
    assert t.custom_sections[1].entries[0].bullets == ["Taught Python to teens"]           # restored


def test_contract_protects_headings():
    assert "keep \"section_titles\" and every custom section's \"id\" and \"title\" EXACTLY" \
        in resume_tailor.HONESTY_CORE


# ── diff and accept/reject ────────────────────────────────────

def _tailored_certs(bullet=None, title=None):
    t = json.loads(json.dumps(WITH_CUSTOM))
    if bullet:
        t["custom_sections"][0]["entries"][0]["bullets"] = [bullet]
    if title:
        t["custom_sections"][0]["entries"][0]["title"] = title
    return t


def test_a_custom_bullet_edit_is_tracked_under_its_heading():
    (c,) = resume_tailor.diff_structured(_s(WITH_CUSTOM), _s(_tailored_certs(bullet="Pro level, cloud design")))
    assert c["path"] == "custom_sections/0/entries/0/bullets/0"
    assert c["section"] == "Certifications"
    assert (c["before"], c["after"]) == ("Professional level", "Pro level, cloud design")


def test_a_custom_entry_title_change_is_flagged_factual():
    (c,) = resume_tailor.diff_structured(_s(WITH_CUSTOM), _s(_tailored_certs(title="AWS SA Pro (2023)")))
    assert c["type"] == "factual"


@pytest.mark.parametrize("decision,expected", [
    ("accepted", "Pro level, cloud design"), ("pending", "Pro level, cloud design"),
    ("rejected", "Professional level"),
])
def test_accept_reject_applies_to_custom_sections(decision, expected):
    st = resume_tailor.build_tailor_state(_s(WITH_CUSTOM), _s(_tailored_certs(bullet="Pro level, cloud design")),
                                          [], "m", {})
    for c in st["changes"]:
        c["decision"] = decision
    eff = resume_tailor.effective_resume(st)
    assert eff["custom_sections"][0]["entries"][0]["bullets"] == [expected]


def test_section_move_card_uses_the_resumes_headings():
    orig = _s({**WITH_CUSTOM, "section_order": ["summary", "projects", "experience"]})
    moved = _s({**WITH_CUSTOM, "section_order": ["summary", "experience", "projects"]})
    (c,) = resume_tailor.diff_structured(orig, moved)
    assert c["after_items"][:3] == ["Summary", "Experience", "Current Stuff"]
    assert "Certifications" in c["after_items"]


# ── arrows ────────────────────────────────────────────────────

def test_arrows_move_custom_sections(client, db, monkeypatch):
    _seed(db, text=TEXT)
    from app import models
    from .conftest import TEST_USER_ID
    prof = db.query(models.Profile).filter_by(user_id=TEST_USER_ID).one()
    prof.resume_structured = _s(WITH_CUSTOM).model_dump()
    db.commit()
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda s, *a, **k: (s, []))
    client.post(f"/jobs/{rid}/tailor-resume")

    order = ["custom:certifications", "summary", "skills", "experience", "education",
             "projects", "custom:volunteering"]
    r = client.put(f"/jobs/{rid}/tailor-resume/section-order", json={"order": order})
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["section_order"] == order

    missing = [k for k in order if k != "custom:volunteering"]
    assert client.put(f"/jobs/{rid}/tailor-resume/section-order",
                      json={"order": missing}).status_code == 400


def test_old_parses_are_marked_for_reparse():
    """0029 keys on the custom_sections key, which every new parse writes."""
    assert "custom_sections" in _s(ORIGINAL).model_dump()
