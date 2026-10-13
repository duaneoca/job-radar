"""Recognising a re-listed job (production 2026-10: 2,847 extra copies by
company + title for one user, each AI-scored at their expense)."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app import dedup, models
from app.merge_duplicates import Row, plan

from .conftest import TEST_USER_ID


# ── match_key ─────────────────────────────────────────────────

def test_spelling_noise_is_ignored():
    a = dedup.match_key("ServiceNow", "Forward Deployed Solution Engineer – Applied AI FDE", "Santa Clara, CA")
    b = dedup.match_key("Servicenow", "Forward Deployed Solution Engineer - AppliedAI FDE",
                        "Santa Clara, Santa Clara County")
    assert a == b == "servicenow|forwarddeployedsolutionengineerappliedaifde|santaclara"


def test_city_keeps_openings_apart():
    assert dedup.match_key("Acme", "Architect", "Boston, MA") != dedup.match_key("Acme", "Architect", "Chicago, IL")


@pytest.mark.parametrize("company,title", [("", "Engineer"), ("Acme", ""), ("–", "!!")])
def test_blank_company_or_title_has_no_key(company, title):
    assert dedup.match_key(company, title, "X") is None


# ── ingest ────────────────────────────────────────────────────

def _post(client, **over):
    body = {"title": "Partner Technical Architect", "company": "ServiceNow", "location": "Boston, MA",
            "url": "https://x/1", "source": "adzuna", "external_id": "a1", "description": "d"}
    body.update(over)
    with patch("app.routers.jobs._celery") as celery:
        r = client.post(f"/jobs?user_id={TEST_USER_ID}", json=body)
    return r, celery


def _reviews(db):
    return db.query(models.UserJobReview).filter_by(user_id=TEST_USER_ID).count()


def test_a_relisting_adds_nothing_and_costs_nothing(client, db):
    _post(client)
    r, celery = _post(client, external_id="a2", url="https://x/2")         # Adzuna re-post
    assert r.status_code == 200
    assert _reviews(db) == 1
    celery.send_task.assert_not_called()                                    # not scored again
    assert db.query(models.Job).count() == 1                                # no orphan row


def test_another_city_is_another_job(client, db):
    _post(client)
    _post(client, external_id="a2", url="https://x/2", location="Chicago, IL")
    assert _reviews(db) == 2


def test_company_boards_are_never_skipped(client, db):
    """Same title twice on a company board is usually two reqs."""
    _post(client, source="greenhouse", external_id="g1")
    _post(client, source="greenhouse", external_id="g2", url="https://x/2")
    assert _reviews(db) == 2


def test_an_aggregator_copy_of_a_board_job_is_skipped(client, db):
    _post(client, source="greenhouse", external_id="g1")
    _post(client, source="adzuna", external_id="a9", url="https://x/9")
    assert _reviews(db) == 1


def test_new_jobs_get_a_key(client, db):
    _post(client)
    assert db.query(models.Job).one().match_key == "servicenow|partnertechnicalarchitect|boston"


# ── merging existing copies ───────────────────────────────────

T0 = datetime(2026, 10, 1)


def _row(n, *, status="reviewed", source="jsearch", url="u", key="k", days=0, **work):
    return Row(review_id=f"r{n}", job_id=f"j{n}", status=status, source=source, url=url,
               match_key=key, scraped=T0 + timedelta(days=days), work=work)


def test_untouched_copies_keep_the_freshest():
    p = plan([_row(1, days=0), _row(2, days=5), _row(3, days=2)])
    assert sorted(r.review_id for r in p.delete) == ["r1", "r3"]


def test_the_copy_you_worked_on_is_kept():
    p = plan([_row(1, days=9), _row(2, status="applied"), _row(3, days=5)])
    assert sorted(r.review_id for r in p.delete) == ["r1", "r3"]


@pytest.mark.parametrize("work", [{"notes": "call Sam"}, {"resume_tailor": {"x": 1}},
                                  {"application_answers": ["a"]}])
def test_any_kind_of_work_counts(work):
    p = plan([_row(1, days=9), _row(2, **work)])
    assert [r.review_id for r in p.delete] == ["r1"]


def test_a_dismissal_is_a_verdict_and_is_kept():
    """Deleting the dismissed copy would bring the job back."""
    p = plan([_row(1, status="dismissed"), _row(2, days=3)])
    assert [r.review_id for r in p.delete] == ["r2"]


def test_two_worked_copies_leave_the_group_alone():
    p = plan([_row(1, status="applied"), _row(2, notes="x"), _row(3)])
    assert p.delete == [] and len(p.skipped) == 1


def test_board_and_manual_jobs_are_never_deleted():
    p = plan([_row(1, source="greenhouse", url="g"), _row(2, source="adzuna", url="a", days=9)])
    assert [r.review_id for r in p.delete] == ["r2"]          # the board one is kept


def test_link_copies_then_key_copies():
    rows = [_row(1, url="same", key="k1"), _row(2, url="same", key="k1", days=1),
            _row(3, source="adzuna", url="other", key="k1", days=2)]
    p = plan(rows)
    assert sorted(r.review_id for r in p.delete) == ["r1", "r2"]
    assert p.by_kind == {"same link (JSearch)": 1, "same company+title+city": 1}
