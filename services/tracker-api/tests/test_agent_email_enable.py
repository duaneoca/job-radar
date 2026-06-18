"""Enabling the agent requires all five folders/labels set + verified (per provider)."""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app import models
from app.config import settings
from app.routers import agent as agent_router
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID

FULL = {"root": "R", "interaction": "I", "postings": "P", "social": "S", "unprocessed": "U"}
PARTIAL = {"root": "R", "interaction": "I", "postings": None, "social": "", "unprocessed": None}


@pytest.fixture(autouse=True)
def enc(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


def _seed(db, provider, blob):
    db.add(models.EmailCredential(
        user_id=TEST_USER_ID, provider=provider,
        encrypted_blob=encrypt_api_key(json.dumps(blob)), enabled=False,
    ))
    db.commit()


def _seed_gmail(db):
    _seed(db, models.EmailProvider.GMAIL, {"refresh_token": "rt", "scopes": ["x"]})


def _seed_imap(db):
    _seed(db, models.EmailProvider.IMAP,
          {"provider": "imap", "host": "h", "port": 993, "username": "u", "password": "p", "use_ssl": True})


def test_enable_requires_all_folders(client, db, monkeypatch):
    _seed_gmail(db)
    monkeypatch.setattr(agent_router, "_verify_gmail_labels", lambda *a, **k: None)
    r = client.put("/agent/email-credentials", json={"folders": PARTIAL, "enabled": True})
    assert r.status_code == 400
    assert "Postings" in r.json()["detail"] and "Social" in r.json()["detail"]
    assert client.get("/agent/email-credentials").json()["enabled"] is False


def test_enable_gmail_verifies_labels(client, db, monkeypatch):
    _seed_gmail(db)
    seen = {}
    monkeypatch.setattr(agent_router, "_verify_gmail_labels", lambda cred, names: seen.update(names=names))
    r = client.put("/agent/email-credentials", json={"folders": FULL, "enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert seen["names"] == ["R", "I", "P", "S", "U"]


def test_enable_gmail_missing_label_blocks(client, db, monkeypatch):
    _seed_gmail(db)

    def boom(*a, **k):
        raise HTTPException(status_code=400, detail="These Gmail labels don't exist (create them first): R")
    monkeypatch.setattr(agent_router, "_verify_gmail_labels", boom)
    r = client.put("/agent/email-credentials", json={"folders": FULL, "enabled": True})
    assert r.status_code == 400
    assert "don't exist" in r.json()["detail"]
    assert client.get("/agent/email-credentials").json()["enabled"] is False


def test_disable_allows_partial_no_verify(client, db, monkeypatch):
    _seed_gmail(db)
    # verifier must NOT be called when disabling
    monkeypatch.setattr(agent_router, "_verify_gmail_labels",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not verify")))
    r = client.put("/agent/email-credentials", json={"folders": PARTIAL, "enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_enable_imap_verifies_stored(client, db, monkeypatch):
    _seed_imap(db)
    monkeypatch.setattr(agent_router, "_verify_imap_stored", lambda *a, **k: None)
    r = client.put("/agent/email-credentials", json={"folders": FULL, "enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
