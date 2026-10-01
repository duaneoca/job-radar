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


# ── Shared relay senders (INTEGRATION_SPEC §3.7 relay rule) ───────────────────
# One address, many people: every LinkedIn InMail recruiter writes from the same
# relay, and replying to it reaches nobody. Grouped by address they all merged
# into one suggestion; they must be grouped by person and offered with no email.

INMAIL = "inmail-hit-reply@linkedin.com"


def test_relay_recruiters_are_separate_people(client, db):
    _seed_recruiter_email(db, f"Jane Smith <{INMAIL}>", message_prefix="a",
                          card={"name": "Jane Smith",
                                "linkedin_url": "https://www.linkedin.com/in/janesmith"})
    _seed_recruiter_email(db, f"Raj Patel <{INMAIL}>", message_prefix="b", n=2,
                          card={"name": "Raj Patel"})
    sugg = client.get("/recruiters/suggestions").json()
    assert [(s["name"], s["email"], s["email_count"]) for s in sugg] == [
        ("Raj Patel", None, 2), ("Jane Smith", None, 1),
    ]
    assert sugg[1]["linkedin_url"] == "https://www.linkedin.com/in/janesmith"


def test_relay_without_a_card_uses_the_display_name(client, db):
    _seed_recruiter_email(db, f"Jane Smith via LinkedIn <{INMAIL}>")
    s = client.get("/recruiters/suggestions").json()[0]
    assert s["name"] == "Jane Smith" and s["email"] is None


def test_same_profile_merges_across_name_spellings(client, db):
    li = "https://www.linkedin.com/in/janesmith"
    _seed_recruiter_email(db, INMAIL, message_prefix="a", card={"name": "Jane Smith", "linkedin_url": li})
    _seed_recruiter_email(db, INMAIL, message_prefix="b", card={"name": "Jane A. Smith", "linkedin_url": li + "/"})
    sugg = client.get("/recruiters/suggestions").json()
    assert len(sugg) == 1 and sugg[0]["email_count"] == 2


def test_card_with_a_real_address_is_keyed_by_it(client, db):
    """The agent puts the signature's own address in the card when it finds one."""
    _seed_recruiter_email(db, INMAIL, card={"name": "Jane Smith", "email": "jane@agency.com"})
    assert client.get("/recruiters/suggestions").json()[0]["email"] == "jane@agency.com"


def test_noreply_senders_are_shared(client, db):
    _seed_recruiter_email(db, "Acme Talent <no-reply@acme.com>")
    _seed_recruiter_email(db, "jobs-noreply@board.com")           # no name → nothing to offer
    sugg = client.get("/recruiters/suggestions").json()
    assert [(s["name"], s["email"]) for s in sugg] == [("Acme Talent", None)]


def test_dice_relay_is_per_recruiter_and_kept(client, db):
    """Replies to …@user.dice.com reach the recruiter, so it IS their address."""
    _seed_recruiter_email(db, "Bo Lee <abc123@user.dice.com>")
    assert client.get("/recruiters/suggestions").json()[0]["email"] == "abc123@user.dice.com"


def test_relay_recruiter_already_tracked_by_profile(client, db):
    _seed_recruiter_email(db, INMAIL, card={"name": "Jane Smith",
                                            "linkedin_url": "https://linkedin.com/in/JaneSmith/"})
    client.post("/recruiters", json={"name": "J. Smith",
                                     "linkedin_url": "https://www.linkedin.com/in/janesmith"})
    assert client.get("/recruiters/suggestions").json() == []


def test_relay_recruiter_already_tracked_by_name(client, db):
    _seed_recruiter_email(db, f"Jane Smith <{INMAIL}>")
    client.post("/recruiters", json={"name": "jane  smith"})
    assert client.get("/recruiters/suggestions").json() == []


# ── Typed recruiter card on POST /agent/inbox (§3.5 Phase 2) ──────────────────

def _post_inbox(client, **extra):
    body = {"message_id": "<typed-1@x>", "subject": "Role", "sender": "Jane <jane@agency.com>",
            "received_at": "2026-10-01T12:00:00Z", "category": "recruiter_outreach",
            "confidence": 1.0, "postings": [], **extra}
    return client.post("/agent/inbox", json=body, headers={"X-User-Id": str(TEST_USER_ID)})


def test_typed_card_reaches_suggestions(client):
    r = _post_inbox(client, recruiter={**FULL_CARD, "email": "jane@agency.com"})
    assert r.status_code == 201, r.text
    s = client.get("/recruiters/suggestions").json()[0]
    assert s["phone"] == "212 389 9503" and s["type"] == "agency"


def test_typed_card_wins_over_the_nested_copy(client):
    _post_inbox(client, recruiter={"name": "Jane Typed", "email": "jane@agency.com"},
                raw_extracted_json={"recruiter_contact": {"name": "Jane Nested"}, "other": 1})
    assert client.get("/recruiters/suggestions").json()[0]["name"] == "Jane Typed"


def test_nested_card_alone_still_works(client):
    _post_inbox(client, raw_extracted_json={"recruiter_contact": {"name": "Jane Nested",
                                                                  "email": "jane@agency.com"}})
    assert client.get("/recruiters/suggestions").json()[0]["name"] == "Jane Nested"


def test_malformed_typed_card_costs_the_card_not_the_email(client, db):
    """Email-derived and optional: a garbled card must never reject the write."""
    r = _post_inbox(client, recruiter={"represents": "not-a-list"})   # and no name
    assert r.status_code == 201, r.text
    row = db.query(models.InboxEmail).filter_by(message_id="<typed-1@x>").one()
    assert not (row.raw_extracted_json or {}).get("recruiter_contact")
