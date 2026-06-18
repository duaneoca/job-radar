"""IMAP ("Other") mailbox provider — store creds, masked status, config injection."""

import json
import types

from cryptography.fernet import Fernet

from app.config import settings
from app.routers.agent import get_agent_config


IMAP = {"host": "imap.fastmail.com", "port": 993, "username": "me@fastmail.com",
        "password": "s3cret", "use_ssl": True}


def test_set_imap_then_status(client, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    r = client.put("/agent/email-credentials/imap", json=IMAP)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "imap"
    assert body["connected"] is True
    assert body["imap_host"] == "imap.fastmail.com"
    assert body["imap_username"] == "me@fastmail.com"
    # password is never returned
    assert "s3cret" not in json.dumps(body)


def test_imap_config_injection(client, db, test_user, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    client.put("/agent/email-credentials/imap", json=IMAP)
    cfg = get_agent_config(request=types.SimpleNamespace(client=None), db=db, user=test_user)
    blob = cfg.email_credentials
    assert blob["provider"] == "imap"
    assert blob["host"] == "imap.fastmail.com"
    assert blob["password"] == "s3cret"   # decrypted, in-cluster only
    assert cfg.provider == "imap"


def test_imap_then_folders_and_enable(client, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    client.put("/agent/email-credentials/imap", json=IMAP)
    r = client.put("/agent/email-credentials", json={
        "folders": {"root": "JobRadar", "interaction": None, "postings": None,
                    "social": None, "unprocessed": None},
        "enabled": False,
    })
    assert r.status_code == 200
    assert r.json()["folders"]["root"] == "JobRadar"
    assert r.json()["enabled"] is False
    assert r.json()["connected"] is True   # creds untouched
