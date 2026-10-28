"""Filling Add Job from a whole page of text (app/posting_extract.py) — the
bookmarklet's fallback on unknown sites and the "Paste the posting" box."""

import json
from unittest.mock import patch

import pytest

from app import models
from app.posting_extract import annual_pay, slice_description

from .conftest import TEST_USER_ID
from .test_oauth import _bearer, _consent, _fresh_ratelimit, api, web  # noqa: F401 — fixtures

NAV = "Home Careers Search jobs Sign in\n\n"
POSTING = (
    "Senior Platform Engineer\nAcme Robotics · Austin, TX · Hybrid\n\n"
    "About the role\nYou will build   the platform that runs our fleet of warehouse robots.\n"
    "Responsibilities\n• Own the Kubernetes clusters\n• Mentor engineers\n"
    "Pay: $150,000 - $190,000 per year.\n"
    "Acme is an equal opportunity employer and values diversity of every kind."
)
FOOTER = "\n\nSimilar jobs\nStaff Engineer, Robotics\n© 2026 Acme Robotics. Privacy. Terms."
PAGE = NAV + POSTING + FOOTER


def _answer(**over):
    base = {
        "is_job_posting": True, "title": "Senior Platform Engineer", "company": "Acme Robotics",
        "location": "Austin, TX", "remote": False,
        "pay": {"min": 150000, "max": 190000, "period": "year"},
        # Model writes single spaces; the page has a run of three.
        "description_start": "About the role You will build the platform that runs",
        "description_end": "equal opportunity employer and values diversity of every kind.",
    }
    base.update(over)
    return json.dumps(base)


# ── the description is copied from the page, never written by the model ──

def test_slices_the_posting_verbatim_out_of_the_page():
    desc, found = slice_description(PAGE, "About the role You will build the platform that runs",
                                    "values diversity of every kind.")
    assert found
    assert desc.startswith("About the role\nYou will build   the platform")   # page's own whitespace
    assert desc.endswith("values diversity of every kind.")
    assert "Similar jobs" not in desc and "Sign in" not in desc


def test_an_unfound_start_returns_the_whole_text_and_says_so():
    desc, found = slice_description(PAGE, "words that are not on this page at all", None)
    assert not found and desc == PAGE.strip()


def test_an_unfound_end_runs_to_the_end():
    desc, found = slice_description(PAGE, "About the role You will build", "nothing like this anywhere here")
    assert not found and desc.startswith("About the role") and desc.endswith("Terms.")


def test_too_short_a_snippet_is_not_trusted():
    assert slice_description(PAGE, "About", None) == (PAGE.strip(), False)


@pytest.mark.parametrize("pay,expected", [
    ({"min": 150000, "max": 190000, "period": "year"}, (150000, 190000)),
    ({"min": 60, "max": 75, "period": "hour"}, (124800, 156000)),
    ({"min": 10000, "max": 12000, "period": "month"}, (120000, 144000)),
    ({"min": 190000, "max": 150000}, (150000, 190000)),       # swapped, period defaults to year
    ({"min": 120000, "max": None, "period": "year"}, (120000, 120000)),
    ({"min": 45, "max": 50, "period": "year"}, (None, None)),  # an hourly rate mislabelled yearly
    ({"min": "lots", "period": "year"}, (None, None)),
    ({"min": 100000, "period": "week"}, (None, None)),
    (None, (None, None)),
])
def test_pay_becomes_yearly_or_nothing(pay, expected):
    assert annual_pay(pay) == expected


# ── the endpoint ──────────────────────────────────────────────

def _key(db):
    from app.security import encrypt_api_key
    db.add(models.UserAPIKey(user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
                             encrypted_key=encrypt_api_key("sk-test"), preferred_model="claude-haiku-4-5"))
    db.commit()


def test_fills_the_form_from_a_page(db, client):
    _key(db)
    with patch("app.posting_extract.llm_complete", return_value=_answer()) as llm:
        r = client.post("/jobs/extract-posting", json={"text": PAGE, "page_title": "Senior Platform Engineer | Acme"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["title"] == "Senior Platform Engineer" and out["company"] == "Acme Robotics"
    assert (out["salary_min"], out["salary_max"]) == (150000, 190000)
    assert out["description_found"] and out["description"].startswith("About the role")
    # The page title went to the model; nothing was saved.
    assert "Senior Platform Engineer | Acme" in llm.call_args.args[1][0]["content"]
    assert db.query(models.UserJobReview).count() == 0


def test_model_prose_around_the_json_is_tolerated(db, client):
    _key(db)
    with patch("app.posting_extract.llm_complete", return_value="Here you go:\n```json\n" + _answer() + "\n```\nDone."):
        assert client.post("/jobs/extract-posting", json={"text": PAGE}).status_code == 200


def test_a_page_that_isnt_a_posting_says_so(db, client):
    _key(db)
    with patch("app.posting_extract.llm_complete", return_value=_answer(is_job_posting=False)):
        r = client.post("/jobs/extract-posting", json={"text": PAGE})
    assert r.status_code == 422 and "doesn't look like a single job posting" in r.json()["detail"]


def test_an_unreadable_answer_is_a_clear_error(db, client):
    _key(db)
    with patch("app.posting_extract.llm_complete", return_value="I'm sorry, I can't help with that."):
        r = client.post("/jobs/extract-posting", json={"text": PAGE})
    assert r.status_code == 502 and "fill in the form by hand" in r.json()["detail"]


def test_too_little_text_never_calls_the_model(db, client):
    _key(db)
    with patch("app.posting_extract.llm_complete") as llm:
        r = client.post("/jobs/extract-posting", json={"text": "Loading…"})
    assert r.status_code == 422 and not llm.called


def test_fields_are_capped_and_typed(db, client):
    _key(db)
    with patch("app.posting_extract.llm_complete",
               return_value=_answer(title="T" * 5000, company=["not", "a", "string"], remote="yes")):
        out = client.post("/jobs/extract-posting", json={"text": PAGE}).json()
    assert len(out["title"]) == 300 and out["company"] is None and out["remote"] is False


def test_no_key_is_the_usual_400(client):
    with patch("app.posting_extract.llm_complete") as llm:
        assert client.post("/jobs/extract-posting", json={"text": PAGE}).status_code == 400
    assert not llm.called


def test_a_connected_assistant_cannot_use_it(web, api):  # noqa: F811
    tokens, _, _ = _consent(web, api)
    with patch("app.posting_extract.llm_complete") as llm:
        r = api.post("/jobs/extract-posting", headers=_bearer(tokens), json={"text": PAGE})
    assert r.status_code == 403 and not llm.called
