"""Mailbox retention (INTEGRATION_SPEC §3.8): per-user days before Social and
Postings mail goes to Trash, delivered as the optional `retention` block.

The agent clamps negatives but has no upper bound, so the server's 1–365 range
is the only cap — and an absent block must mean "off", the same as an older
Job Radar."""

import json

import pytest
from cryptography.fernet import Fernet

from app import models
from app.config import settings
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID

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


def _retention(client):
    r = client.get(f"/agent/cloud/config/{TEST_USER_ID}", headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200, r.text
    return r.json()["retention"]


def test_off_by_default_and_omitted(client, mailbox):
    assert _retention(client) is None
    assert client.get("/agent/email-credentials").json()["retention"] == {
        "social_days": None, "postings_days": None}


def test_round_trips_into_the_cloud_config(client, mailbox):
    r = client.put("/agent/email-credentials/retention", json={"social_days": 14, "postings_days": 30})
    assert r.status_code == 200, r.text
    assert _retention(client) == {"social_days": 14, "postings_days": 30}


def test_one_folder_on_still_sends_the_block(client, mailbox):
    client.put("/agent/email-credentials/retention", json={"social_days": 7, "postings_days": 0})
    assert _retention(client) == {"social_days": 7, "postings_days": None}


def test_zero_or_blank_turns_it_back_off(client, mailbox):
    client.put("/agent/email-credentials/retention", json={"social_days": 14, "postings_days": 14})
    client.put("/agent/email-credentials/retention", json={"social_days": 0})
    assert _retention(client) is None


@pytest.mark.parametrize("days", [-1, 366, 10_000])
def test_out_of_range_is_refused(client, mailbox, days):
    r = client.put("/agent/email-credentials/retention", json={"social_days": days})
    assert r.status_code == 422


def test_needs_a_connected_mailbox(client):
    assert client.put("/agent/email-credentials/retention", json={"social_days": 14}).status_code == 404
