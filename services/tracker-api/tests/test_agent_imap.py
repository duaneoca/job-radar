"""IMAP ("Other") mailbox provider — verify-on-save, store, status, config injection."""

import json
import types

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.config import settings
from app.routers import agent as agent_router
from app.routers.agent import _assert_public_host, _imap_folder_name, get_agent_config

FOLDERS = {"root": "JobRadar", "interaction": "JobRadar/Interaction",
           "postings": "JobRadar/Postings", "social": "JobRadar/Social",
           "unprocessed": "JobRadar/Unprocessed"}
IMAP = {"host": "imap.fastmail.com", "port": 993, "username": "me@fastmail.com",
        "password": "s3cret", "use_ssl": True, "folders": FOLDERS}


@pytest.fixture
def no_verify(monkeypatch):
    """Skip the live IMAP verification (no network in tests)."""
    monkeypatch.setattr(agent_router, "_verify_imap", lambda *a, **k: None)


# ── store / status / config (verification mocked) ─────────────

def test_set_imap_then_status(client, no_verify, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    r = client.put("/agent/email-credentials/imap", json=IMAP)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "imap"
    assert body["connected"] is True
    assert body["imap_host"] == "imap.fastmail.com"
    assert body["imap_username"] == "me@fastmail.com"
    assert "s3cret" not in json.dumps(body)


def test_imap_stores_folders_in_one_call(client, no_verify, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    r = client.put("/agent/email-credentials/imap", json=IMAP)
    assert r.status_code == 200
    assert r.json()["folders"]["root"] == "JobRadar"
    assert r.json()["folders"]["unprocessed"] == "JobRadar/Unprocessed"


def test_imap_requires_all_folders(client, no_verify, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    # missing social + unprocessed → 400, nothing stored
    r = client.put("/agent/email-credentials/imap", json={
        **IMAP,
        "folders": {"root": "JobRadar", "interaction": "x", "postings": "y",
                    "social": "", "unprocessed": None},
    })
    assert r.status_code == 400
    assert "Social" in r.json()["detail"] and "Unprocessed" in r.json()["detail"]
    assert client.get("/agent/email-credentials").json()["connected"] is False


def test_imap_no_folders_rejected(client, no_verify, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    r = client.put("/agent/email-credentials/imap", json={k: v for k, v in IMAP.items() if k != "folders"})
    assert r.status_code == 400
    assert "required" in r.json()["detail"].lower()


def test_imap_config_injection(client, db, test_user, no_verify, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    client.put("/agent/email-credentials/imap", json=IMAP)
    cfg = get_agent_config(request=types.SimpleNamespace(client=None), db=db, user=test_user)
    blob = cfg.email_credentials
    assert blob["provider"] == "imap"
    assert blob["host"] == "imap.fastmail.com"
    assert blob["password"] == "s3cret"   # decrypted, in-cluster only


def test_verification_failure_blocks_save(client, db, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())

    def boom(*a, **k):
        raise HTTPException(status_code=400, detail="Login failed — check the username and password.")
    monkeypatch.setattr(agent_router, "_verify_imap", boom)

    r = client.put("/agent/email-credentials/imap", json=IMAP)
    assert r.status_code == 400
    assert "Login failed" in r.json()["detail"]
    # nothing stored
    assert client.get("/agent/email-credentials").json()["connected"] is False


# ── pure helpers ──────────────────────────────────────────────

def test_imap_folder_name_parsing():
    assert _imap_folder_name(b'(\\HasNoChildren) "/" "INBOX"') == "INBOX"
    assert _imap_folder_name(b'(\\HasNoChildren) "/" "Hire Duane/Postings"') == "Hire Duane/Postings"
    assert _imap_folder_name(b'(\\HasChildren) "." Folders') == "Folders"


def test_parse_imap_list_line_delimiter_and_name():
    from app.routers.agent import _parse_imap_list_line
    # "/" delimiter, prefixed name (the Proton-style case that broke leaf matching)
    assert _parse_imap_list_line(b'(\\HasNoChildren) "/" "Folders/Postings"') == ("/", "Folders/Postings")
    # "." delimiter (Dovecot/Courier), unquoted name
    assert _parse_imap_list_line(b'(\\HasChildren) "." INBOX.Postings') == (".", "INBOX.Postings")
    # NIL delimiter (flat namespace) → None
    assert _parse_imap_list_line(b'(\\Noselect) NIL "Archive"') == (None, "Archive")


def test_assert_public_host_blocks_private(monkeypatch):
    # host resolves to a loopback/private address → rejected (SSRF guard)
    monkeypatch.setattr(agent_router.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(HTTPException) as e:
        _assert_public_host("evil.internal")
    assert e.value.status_code == 400


def test_assert_public_host_allows_public(monkeypatch):
    monkeypatch.setattr(agent_router.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("151.101.0.1", 0))])
    assert _assert_public_host("imap.fastmail.com") is None
