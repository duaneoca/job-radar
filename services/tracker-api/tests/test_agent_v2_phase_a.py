"""Agent V2 Phase A (INTEGRATION_SPEC §3.6): dedup_key is stored, and the
per-user email policy round-trips from Settings into the cloud config.

Phase A enforces nothing — these pin that the key is no longer silently
dropped, and that the policy reaches the agent in exactly the shape it reads
(None fields → the agent's own defaults; [] domains → allow any sender)."""

import json
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app import models
from app.config import settings
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID

AGENT = {"X-User-Id": str(TEST_USER_ID)}
TOKEN = "internal-token-abc"


@pytest.fixture
def mailbox(db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    db.add(models.EmailCredential(
        user_id=TEST_USER_ID, provider=models.EmailProvider.GMAIL, enabled=True,
        encrypted_blob=encrypt_api_key(json.dumps({"refresh_token": "rt"})),
    ))
    db.commit()


def _cloud_config(client):
    r = client.get(f"/agent/cloud/config/{TEST_USER_ID}", headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200, r.text
    return r.json()


# ── dedup_key ──────────────────────────────────────────────────

def _post(client, postings):
    r = client.post("/agent/inbox", headers=AGENT, json={
        "message_id": f"<{uuid.uuid4()}@x>", "subject": "Jobs for you",
        "sender": "LinkedIn <jobs@linkedin.com>", "received_at": datetime.now(timezone.utc).isoformat(),
        "category": "job_alert", "confidence": 0.99, "postings": postings,
    })
    assert r.status_code == 201, r.text
    return r.json()["posting_ids"]


def test_dedup_key_is_stored_not_dropped(client, db):
    [pid] = _post(client, [{"company": "Acme", "role": "Eng", "link": "https://x.example/1",
                            "dedup_key": "  linkedin:4012345678  "}])
    assert db.get(models.InboxPosting, uuid.UUID(pid)).dedup_key == "linkedin:4012345678"


def test_dedup_key_is_optional_and_capped(client, db):
    a, b = _post(client, [{"company": "A", "role": "R"},
                          {"company": "B", "role": "R", "dedup_key": "ct:" + "x" * 1000}])
    assert db.get(models.InboxPosting, uuid.UUID(a)).dedup_key is None
    assert len(db.get(models.InboxPosting, uuid.UUID(b)).dedup_key) == 300




# ── email policy ───────────────────────────────────────────────

def test_unset_policy_is_omitted_from_the_cloud_config(client, mailbox):
    """An older Job Radar sends no email_policy; an unset one must look the same."""
    assert _cloud_config(client)["email_policy"] is None
    assert client.get("/agent/email-credentials").json()["email_policy"] == {
        "allowed_sender_domains": None, "require_sender_auth": None, "zero_postings_action": None,
        "trusted_authserv_ids": None}


def test_policy_round_trips_into_the_cloud_config(client, mailbox):
    r = client.put("/agent/email-credentials/policy", json={
        "allowed_sender_domains": [" LinkedIn.com ", "@glassdoor.com", "linkedin.com", ""],
        "require_sender_auth": False, "zero_postings_action": "Unprocessed"})
    assert r.status_code == 200, r.text
    policy = _cloud_config(client)["email_policy"]
    assert policy == {"allowed_sender_domains": ["linkedin.com", "glassdoor.com"],
                      "require_sender_auth": False, "zero_postings_action": "unprocessed",
                      "trusted_authserv_ids": None}


def test_empty_domain_list_means_allow_any_and_survives(client, mailbox):
    """[] is a real setting, not "unset" — it must reach the agent as []."""
    client.put("/agent/email-credentials/policy", json={"allowed_sender_domains": []})
    assert _cloud_config(client)["email_policy"]["allowed_sender_domains"] == []


def test_none_resets_a_field_to_the_agent_default(client, mailbox):
    client.put("/agent/email-credentials/policy", json={"zero_postings_action": "unprocessed"})
    client.put("/agent/email-credentials/policy", json={})
    assert _cloud_config(client)["email_policy"] is None


@pytest.mark.parametrize("body", [
    {"zero_postings_action": "delete"},
    {"allowed_sender_domains": ["not a domain"]},
    {"allowed_sender_domains": ["javascript:alert(1)"]},
    {"allowed_sender_domains": [f"d{i}.example.com" for i in range(101)]},
])
def test_bad_policy_fails_on_save_not_in_the_agent(client, mailbox, body):
    """The agent fails a user's whole run on an unknown action — refuse it here."""
    assert client.put("/agent/email-credentials/policy", json=body).status_code == 422


def test_policy_needs_a_connected_mailbox(client):
    r = client.put("/agent/email-credentials/policy", json={"require_sender_auth": True})
    assert r.status_code == 404


# ── trusted mail servers (non-Gmail/Proton IMAP users) ─────────

def test_trusted_authserv_ids_reach_the_agent(client, mailbox):
    """With require_sender_auth on and only the agent's defaults (Proton, Gmail),
    a user on any other IMAP host has every email rejected. They name theirs."""
    client.put("/agent/email-credentials/policy", json={
        "trusted_authserv_ids": [" Mx.Fastmail.com ", "mx.fastmail.com", "mail.example.org"]})
    assert _cloud_config(client)["email_policy"]["trusted_authserv_ids"] == [
        "mx.fastmail.com", "mail.example.org"]


def test_empty_trusted_list_means_defaults_and_is_stored_as_unset(client, mailbox):
    """The agent reads [] as "use defaults" (unlike the domain list), so storing
    [] would claim a setting that has no effect."""
    client.put("/agent/email-credentials/policy", json={"trusted_authserv_ids": []})
    assert _cloud_config(client)["email_policy"] is None


def test_bad_trusted_server_name_is_refused(client, mailbox):
    r = client.put("/agent/email-credentials/policy", json={"trusted_authserv_ids": ["not a host"]})
    assert r.status_code == 422
