"""Recruiter CRM — CRUD, job links (SET NULL on delete), and inbox suggestions."""

from datetime import datetime, timezone
from unittest.mock import patch

from app import models

from .conftest import TEST_USER_ID

JOB = {"title": "Eng", "company": "Acme Corp", "url": "https://x/1", "source": "manual"}


def _scrape(client, **over):
    with patch("app.routers.jobs._celery"):
        return client.post(f"/jobs?user_id={TEST_USER_ID}", json={**JOB, **over})


def _first_review_id(client) -> str:
    return client.get("/jobs").json()["items"][0]["id"]


def _seed_recruiter_email(db, sender, *, n=1, message_prefix="m", card=None):
    raw = {"recruiter_contact": card} if card is not None else None
    for i in range(n):
        db.add(models.InboxEmail(
            user_id=TEST_USER_ID,
            message_id=f"{message_prefix}-{sender}-{i}",
            subject="Great opportunity",
            sender=sender,
            received_at=datetime.now(timezone.utc),
            category=models.EmailCategory.RECRUITER_OUTREACH,
            confidence=0.9,
            raw_extracted_json=raw,
            status=models.EmailStatus.PROCESSED,
        ))
    db.commit()


# ── CRUD ──────────────────────────────────────────────────────

def test_create_and_list(client):
    r = client.post("/recruiters", json={
        "name": "Jane Recruiter", "email": "jane@agency.com",
        "employer": "Best Agency", "type": "agency",
        "companies_represented": ["Acme", "Globex"],
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Jane Recruiter"
    assert body["status"] == "active"            # default
    assert body["companies_represented"] == ["Acme", "Globex"]
    assert body["jobs"] == []

    lst = client.get("/recruiters").json()
    assert len(lst) == 1
    assert lst[0]["email"] == "jane@agency.com"


def test_partial_update(client):
    rid = client.post("/recruiters", json={"name": "Jane"}).json()["id"]
    r = client.patch(f"/recruiters/{rid}", json={"status": "ghosted", "phone": "555-1212"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ghosted"
    assert body["phone"] == "555-1212"
    assert body["name"] == "Jane"                # untouched


def test_invalid_status_rejected(client):
    r = client.post("/recruiters", json={"name": "X", "status": "nonsense"})
    assert r.status_code == 422


def test_search_by_name_or_employer(client):
    client.post("/recruiters", json={"name": "Alice", "employer": "Hooli"})
    client.post("/recruiters", json={"name": "Bob", "employer": "Pied Piper"})
    assert len(client.get("/recruiters", params={"search": "hooli"}).json()) == 1
    assert len(client.get("/recruiters", params={"search": "Bob"}).json()) == 1
    assert len(client.get("/recruiters", params={"search": "zzz"}).json()) == 0


def test_status_filter(client):
    client.post("/recruiters", json={"name": "A", "status": "active"})
    client.post("/recruiters", json={"name": "G", "status": "ghosted"})
    only = client.get("/recruiters", params={"status": "ghosted"}).json()
    assert [r["name"] for r in only] == ["G"]


# ── Job links ─────────────────────────────────────────────────

def test_link_and_unlink_job(client):
    rid = client.post("/recruiters", json={"name": "Jane"}).json()["id"]
    _scrape(client, company="Acme Corp")
    review_id = _first_review_id(client)

    linked = client.post(f"/recruiters/{rid}/jobs", json={"review_id": review_id}).json()
    assert len(linked["jobs"]) == 1
    assert linked["jobs"][0]["company"] == "Acme Corp"

    # The job list now reports the linked recruiter
    job = client.get("/jobs").json()["items"][0]
    assert job["recruiter_id"] == rid
    assert job["recruiter_name"] == "Jane"

    unlinked = client.delete(f"/recruiters/{rid}/jobs/{review_id}").json()
    assert unlinked["jobs"] == []
    assert client.get("/jobs").json()["items"][0]["recruiter_id"] is None


def test_delete_recruiter_unlinks_but_keeps_job(client, db):
    rid = client.post("/recruiters", json={"name": "Jane"}).json()["id"]
    _scrape(client, company="Acme Corp")
    review_id = _first_review_id(client)
    client.post(f"/recruiters/{rid}/jobs", json={"review_id": review_id})

    assert client.delete(f"/recruiters/{rid}").status_code == 204

    # Job still exists, just unlinked
    items = client.get("/jobs").json()["items"]
    assert len(items) == 1
    assert items[0]["recruiter_id"] is None


def test_link_foreign_job_404(client):
    rid = client.post("/recruiters", json={"name": "Jane"}).json()["id"]
    r = client.post(f"/recruiters/{rid}/jobs",
                    json={"review_id": "00000000-0000-0000-0000-0000000000ff"})
    assert r.status_code == 404


# ── Suggestions from inbox ────────────────────────────────────

def test_suggestions_group_and_rank(client, db):
    _seed_recruiter_email(db, "Jane Smith <jane@agency.com>", n=3)
    _seed_recruiter_email(db, "bob@pp.com", n=1)
    sugg = client.get("/recruiters/suggestions").json()
    assert [s["email"] for s in sugg] == ["jane@agency.com", "bob@pp.com"]  # by count
    assert sugg[0]["name"] == "Jane Smith"
    assert sugg[0]["email_count"] == 3
    assert sugg[1]["name"] == "bob"          # email-local fallback when no display name


def test_suggestions_exclude_already_tracked(client, db):
    _seed_recruiter_email(db, "Jane Smith <jane@agency.com>", n=2)
    client.post("/recruiters", json={"name": "Jane", "email": "jane@agency.com"})
    assert client.get("/recruiters/suggestions").json() == []


def test_suggestions_ignore_non_recruiter_emails(client, db):
    db.add(models.InboxEmail(
        user_id=TEST_USER_ID, message_id="x1", subject="s", sender="alerts@board.com",
        received_at=datetime.now(timezone.utc),
        category=models.EmailCategory.JOB_ALERT, confidence=0.9,
        status=models.EmailStatus.PROCESSED,
    ))
    db.commit()
    assert client.get("/recruiters/suggestions").json() == []


# ── Suggestions enriched from the agent's recruiter_contact card ──────────────

FULL_CARD = {
    "name": "Nishant Vij", "email": "nishant.vij@testingxperts.com",
    "phone": "212 389 9503", "employer": "TestingXperts",
    "title": "Staffing Specialist", "is_agency": True,
    "linkedin_url": "https://www.linkedin.com/in/nishantvij",
    "represents": ["Acme Corp"], "recruiter_confidence": 0.95,
}


def test_suggestion_enriched_from_card(client, db):
    _seed_recruiter_email(db, "Nishant Vij <nishant.vij@testingxperts.com>", card=FULL_CARD)
    s = client.get("/recruiters/suggestions").json()[0]
    assert s["name"] == "Nishant Vij"
    assert s["email"] == "nishant.vij@testingxperts.com"
    assert s["phone"] == "212 389 9503"
    assert s["title"] == "Staffing Specialist"
    assert s["employer"] == "TestingXperts"
    assert s["linkedin_url"] == "https://www.linkedin.com/in/nishantvij"
    assert s["type"] == "agency"                       # is_agency True
    assert s["companies_represented"] == ["Acme Corp"]
    assert s["recruiter_confidence"] == 0.95


def test_is_agency_false_maps_in_house(client, db):
    _seed_recruiter_email(db, "ip@acme.com", card={"name": "IP", "email": "ip@acme.com", "is_agency": False})
    assert client.get("/recruiters/suggestions").json()[0]["type"] == "in_house"


def test_is_agency_absent_leaves_type_null(client, db):
    _seed_recruiter_email(db, "x@y.com", card={"name": "X", "email": "x@y.com"})
    assert client.get("/recruiters/suggestions").json()[0]["type"] is None


def test_partial_card_omits_missing(client, db):
    """A card with only some fields (the agent omits unknowns) surfaces just those."""
    _seed_recruiter_email(db, "Jo <jo@firm.com>",
                          card={"name": "Jo", "email": "jo@firm.com", "employer": "Firm", "is_agency": True})
    s = client.get("/recruiters/suggestions").json()[0]
    assert s["employer"] == "Firm" and s["type"] == "agency"
    assert s["phone"] is None and s["title"] is None and s["linkedin_url"] is None


def test_unsafe_linkedin_url_dropped(client, db):
    _seed_recruiter_email(db, "bad@firm.com",
                          card={"name": "Bad", "email": "bad@firm.com", "linkedin_url": "javascript:alert(1)"})
    assert client.get("/recruiters/suggestions").json()[0]["linkedin_url"] is None


def test_card_email_preferred_for_dedup(client, db):
    """Card email keys the suggestion; tracking that email excludes it."""
    _seed_recruiter_email(db, "Display Name <noreply@bounce.com>",
                          card={"name": "Real", "email": "real@agency.com"})
    assert client.get("/recruiters/suggestions").json()[0]["email"] == "real@agency.com"
    client.post("/recruiters", json={"name": "Real", "email": "real@agency.com"})
    assert client.get("/recruiters/suggestions").json() == []


def test_most_complete_card_wins(client, db):
    addr = "Pat <pat@agency.com>"
    _seed_recruiter_email(db, addr, message_prefix="thin", card={"name": "Pat", "email": "pat@agency.com"})
    _seed_recruiter_email(db, addr, message_prefix="rich",
                          card={"name": "Pat", "email": "pat@agency.com", "phone": "555", "title": "Recruiter"})
    s = client.get("/recruiters/suggestions").json()[0]
    assert s["email_count"] == 2
    assert s["phone"] == "555" and s["title"] == "Recruiter"


def test_create_recruiter_with_title(client):
    """The title field round-trips through create + list (new column)."""
    r = client.post("/recruiters", json={"name": "T", "title": "Lead Recruiter"})
    assert r.status_code == 201
    assert r.json()["title"] == "Lead Recruiter"
    assert client.get("/recruiters").json()[0]["title"] == "Lead Recruiter"
