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


# ── Print/format settings (Phase 4 knobs) ─────────────────────

def test_print_settings_persist_and_sanitize(client, db, monkeypatch):
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))
    client.post(f"/jobs/{rid}/tailor-resume")

    r = client.put(f"/jobs/{rid}/tailor-resume/print-settings", json={"settings": {
        "template": "modern", "fontPt": 99, "density": "weird",
        "marginIn": 0.5, "accent": "red; }body{evil}", "forceBreakBefore": ["a", "b"]}})
    assert r.status_code == 200
    s = r.json()
    assert s["template"] == "modern"
    assert s["fontPt"] == 12.0          # clamped to the max
    assert s["density"] == "normal"     # invalid → default
    assert s["accent"] is None          # not a clean hex → dropped (CSS safety)
    assert s["forceBreakBefore"] == ["a", "b"]

    # Surfaced as the per-résumé override on the tailor GET.
    got = client.get(f"/jobs/{rid}/tailor-resume").json()
    assert got["print_settings"]["template"] == "modern"
    assert got["default_print_settings"] is None     # no profile default set yet


def test_profile_default_template_settings(client, db):
    db.add(models.Profile(user_id=TEST_USER_ID, name="default", is_active=True, resume_text="x"))
    db.commit()
    r = client.put("/profile/resume-template-settings", json={"settings": {
        "template": "classic", "fontPt": 10.5, "density": "compact",
        "marginIn": 0.6, "accent": "#1f3a5f"}})
    assert r.status_code == 200
    assert r.json()["fontPt"] == 10.5
    assert r.json()["accent"] == "#1f3a5f"           # valid hex kept
    assert client.get("/profile").json()["resume_template_settings"]["density"] == "compact"


# ── reorder-aware diff ────────────────────────────────────────

def _with_bullets(bullets, base=None):
    d = base or ORIGINAL
    return {**d, "experience": [{**d["experience"][0], "bullets": list(bullets)}]}


def _swapped():
    return _with_bullets(["Led migration", "Built ETL pipelines"])


def test_pure_swap_emits_one_reorder_change():
    """Moving a bullet is ONE change, not a chain of inverse edits."""
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(_swapped()))
    assert len(changes) == 1
    c = changes[0]
    assert c["kind"] == "reordered"
    assert c["type"] == "reorder"
    assert c["path"] == "experience/0/bullets"
    assert c["order"] == [1, 0]                       # tailored slot -> original index
    assert c["before_items"] == ["Built ETL pipelines", "Led migration"]
    assert c["after_items"] == ["Led migration", "Built ETL pipelines"]
    assert c["decision"] == "pending"                 # requires explicit accept


def test_move_and_reword_emits_reorder_plus_modified():
    """A bullet that moved AND changed wording gets both cards."""
    tailored = _with_bullets(["Led the migration effort", "Built ETL pipelines"])
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(tailored))
    kinds = sorted(c["kind"] for c in changes)
    assert kinds == ["modified", "reordered"]
    mod = next(c for c in changes if c["kind"] == "modified")
    assert mod["before"] == "Led migration"           # matched by fuzzy, not position
    assert mod["after"] == "Led the migration effort"
    assert mod["orig_index"] == 1 and mod["new_index"] == 0


def test_reorder_card_sorts_above_its_items():
    tailored = _with_bullets(["Led the migration effort", "Built ETL pipelines"])
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(tailored))
    assert changes[0]["kind"] == "reordered"


def test_added_bullet_without_reorder_has_no_reorder_card():
    """Relative order preserved + one insert => added only, no reorder noise."""
    tailored = _with_bullets(["Built ETL pipelines", "Led migration", "Shipped API"])
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(tailored))
    assert [c["kind"] for c in changes] == ["added"]
    assert changes[0]["after"] == "Shipped API"


def test_full_rewrite_stays_modified_not_add_remove():
    """Positional fallback keeps a wholly rewritten bullet a single 'modified'."""
    tailored = _with_bullets(["Totally different text here", "Led migration"])
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(tailored))
    assert [c["kind"] for c in changes] == ["modified"]
    assert changes[0]["before"] == "Built ETL pipelines"


def test_change_id_stable_across_reorder():
    """Ids follow content, so refine keeps the user's accept/reject decisions."""
    reworded = _with_bullets(["Built ETL pipelines v2", "Led migration"])
    moved = _with_bullets(["Led migration", "Built ETL pipelines v2"])
    a = next(c for c in resume_tailor.diff_structured(_s(ORIGINAL), _s(reworded))
             if c["kind"] == "modified")
    b = next(c for c in resume_tailor.diff_structured(_s(ORIGINAL), _s(moved))
             if c["kind"] == "modified")
    assert a["id"] == b["id"]


def test_duplicate_bullets_get_unique_ids():
    dup = _with_bullets(["Same line", "Same line"])
    edited = _with_bullets(["Same line edited", "Same line also edited"])
    changes = resume_tailor.diff_structured(_s(dup), _s(edited))
    ids = [c["id"] for c in changes]
    assert len(ids) == len(set(ids)) == 2


def test_notes_do_not_cross_attach_on_pure_move():
    """A note must not bind to a bullet whose text never changed."""
    notes = [{"before": "Built ETL pipelines", "after": "Built ETL pipelines",
              "type": "vocabulary", "rationale": "should not attach"}]
    changes = resume_tailor.diff_structured(_s(ORIGINAL), _s(_swapped()), notes)
    assert changes[0]["rationale"] == "Bullets re-ordered within this section."


def test_reorder_note_enriches_the_reorder_card():
    notes = [{"type": "reorder", "rationale": "lead with the migration work",
              "trigger": "large-scale migrations"}]
    c = resume_tailor.diff_structured(_s(ORIGINAL), _s(_swapped()), notes)[0]
    assert c["rationale"] == "lead with the migration work"
    assert c["trigger"] == "large-scale migrations"


# ── effective résumé ──────────────────────────────────────────

def _state(original, tailored, decisions=None):
    st = resume_tailor.build_tailor_state(_s(original), _s(tailored), [], "m", {})
    for c in st["changes"]:
        c["decision"] = (decisions or {}).get(c["kind"], "pending")
    return st


def _bullets(resume):
    return resume["experience"][0]["bullets"]


def test_effective_reorder_and_wording_matrix():
    """All four accept/reject combinations of a moved-and-reworded bullet."""
    tailored = _with_bullets(["Led the migration effort", "Built ETL pipelines"])
    orig_order = ["Built ETL pipelines", "Led migration"]

    both_rejected = _state(ORIGINAL, tailored, {"reordered": "rejected", "modified": "rejected"})
    assert _bullets(resume_tailor.effective_resume(both_rejected)) == orig_order

    wording_only = _state(ORIGINAL, tailored, {"reordered": "rejected", "modified": "accepted"})
    assert _bullets(resume_tailor.effective_resume(wording_only)) == \
        ["Built ETL pipelines", "Led the migration effort"]

    # Previously inexpressible: keep the new ORDER but the original wording.
    order_only = _state(ORIGINAL, tailored, {"reordered": "accepted", "modified": "rejected"})
    assert _bullets(resume_tailor.effective_resume(order_only)) == \
        ["Led migration", "Built ETL pipelines"]

    both_accepted = _state(ORIGINAL, tailored, {"reordered": "accepted", "modified": "accepted"})
    assert resume_tailor.effective_resume(both_accepted) == both_accepted["tailored"]


def test_effective_pending_keeps_tailored():
    """Pending behaves like every other pending change — tailored content stands."""
    st = _state(ORIGINAL, _swapped())
    assert _bullets(resume_tailor.effective_resume(st)) == ["Led migration", "Built ETL pipelines"]


def test_effective_rejected_scalar_reverts():
    st = _state(ORIGINAL, TAILORED, {"modified": "rejected"})
    eff = resume_tailor.effective_resume(st)
    assert eff["summary"] == ORIGINAL["summary"]


def test_effective_rejected_skill_items_stays_a_list():
    """The joined 'a · b' display form must never be written back into the array."""
    st = _state(ORIGINAL, TAILORED, {"modified": "rejected"})
    items = resume_tailor.effective_resume(st)["skills"][0]["items"]
    assert items == ["Python", "Java"]


def test_effective_rejected_removal_keeps_the_bullet():
    tailored = _with_bullets(["Built ETL pipelines"])          # dropped one
    st = _state(ORIGINAL, tailored, {"removed": "rejected"})
    assert _bullets(resume_tailor.effective_resume(st)) == \
        ["Built ETL pipelines", "Led migration"]


def test_effective_legacy_state_falls_back():
    """States stored before list-aware diffing still print correctly."""
    legacy = {
        "original": ORIGINAL, "tailored": TAILORED,
        "changes": [{"id": "x", "path": "summary", "before": ORIGINAL["summary"],
                     "after": TAILORED["summary"], "kind": "modified",
                     "type": "wording", "decision": "rejected"}],
    }
    assert resume_tailor.effective_resume(legacy)["summary"] == ORIGINAL["summary"]


def test_build_state_counts_reorders():
    st = resume_tailor.build_tailor_state(_s(ORIGINAL), _s(_swapped()), [], "m", {})
    assert st["reorder_count"] == 1


def test_tailor_response_includes_effective(client, db, monkeypatch):
    """Every tailor response carries the approved résumé, computed server-side."""
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume", lambda *a, **k: (_s(TAILORED), NOTES))

    state = client.post(f"/jobs/{rid}/tailor-resume").json()
    assert state["effective"]["summary"] == TAILORED["summary"]     # pending keeps tailored

    cid = next(c["id"] for c in state["changes"] if c["path"] == "summary")
    r = client.patch(f"/jobs/{rid}/tailor-resume/decisions", json={"decisions": {cid: "rejected"}})
    assert r.json()["effective"]["summary"] == ORIGINAL["summary"]
    assert client.get(f"/jobs/{rid}/tailor-resume").json()["effective"]["summary"] == ORIGINAL["summary"]
    # Never persisted — it is derived from changes + decisions on every read.
    assert "effective" not in (db.query(models.UserJobReview).first().resume_tailor or {})


def test_refine_excludes_reorder_card_from_kept_phrasings(client, db, monkeypatch):
    """A rejected reorder must become an ordering instruction, not a phrasing to keep
    (its `before` is a numbered listing, which would garble the prompt)."""
    _seed(db)
    rid = _scrape(client)
    monkeypatch.setattr(resume_tailor, "tailor_resume",
                        lambda *a, **k: (_s(_swapped()), []))
    state = client.post(f"/jobs/{rid}/tailor-resume").json()
    cid = next(c["id"] for c in state["changes"] if c["kind"] == "reordered")
    client.patch(f"/jobs/{rid}/tailor-resume/decisions", json={"decisions": {cid: "rejected"}})

    seen = {}

    def capture(*a, **k):
        seen["extra"] = k.get("extra", "")
        return _s(_swapped()), []
    monkeypatch.setattr(resume_tailor, "tailor_resume", capture)
    client.post(f"/jobs/{rid}/tailor-resume/refine", json={"instruction": "punchier"})

    assert "Do NOT reorder the bullets" in seen["extra"]
    assert "1. Built ETL pipelines" not in seen["extra"]      # numbered listing not leaked
