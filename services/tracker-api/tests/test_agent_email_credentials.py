"""Tests for cloud Gmail mailbox connect (JR-5): OAuth state, email-credentials
CRUD, and /agent/config refresh_token injection."""

import json
import types

from cryptography.fernet import Fernet

from app import models
from app.config import settings
from app.routers import agent as agent_router
from app.routers.agent import get_agent_config
from app.security import create_oauth_state, decode_oauth_state, encrypt_api_key

from .conftest import TEST_USER_ID


def _seed_gmail_cred(db, *, refresh_token="rt-123", enabled=True, folders=False):
    blob = encrypt_api_key(json.dumps({
        "refresh_token": refresh_token,
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    }))
    cred = models.EmailCredential(
        user_id=TEST_USER_ID, provider=models.EmailProvider.GMAIL,
        encrypted_blob=blob, enabled=enabled,
    )
    if folders:
        cred.folder_root = "Hire Duane"
        cred.folder_interaction = "Hire Duane/Interaction"
    db.add(cred)
    db.commit()
    return cred


# ── OAuth state ───────────────────────────────────────────────

def test_oauth_state_roundtrip():
    state = create_oauth_state("user-abc")
    assert decode_oauth_state(state) == "user-abc"


def test_oauth_state_rejects_plain_access_token():
    """A normal login token must not be accepted as OAuth state (scope guard)."""
    from app.security import create_access_token
    assert decode_oauth_state(create_access_token("user-abc")) is None


# ── email-credentials status / PUT / DELETE ───────────────────

def test_status_when_not_connected(client):
    r = client.get("/agent/email-credentials")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["enabled"] is False
    assert body["provider"] is None


def test_status_when_connected(client, db):
    _seed_gmail_cred(db)
    body = client.get("/agent/email-credentials").json()
    assert body["connected"] is True
    assert body["provider"] == "gmail"
    assert body["enabled"] is True
    # secret never leaks
    assert "refresh_token" not in json.dumps(body)


def test_put_requires_connected_mailbox(client):
    r = client.put("/agent/email-credentials", json={
        "folders": {"root": "X", "interaction": None, "postings": None,
                    "social": None, "unprocessed": None},
        "enabled": True,
    })
    assert r.status_code == 404


def test_put_updates_folders_and_enabled(client, db):
    _seed_gmail_cred(db)
    r = client.put("/agent/email-credentials", json={
        "folders": {"root": "Hire Duane", "interaction": "Hire Duane/Interaction",
                    "postings": "Hire Duane/Postings", "social": None, "unprocessed": None},
        "enabled": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["folders"]["root"] == "Hire Duane"
    assert body["folders"]["postings"] == "Hire Duane/Postings"
    assert body["enabled"] is False
    assert body["connected"] is True  # secret blob untouched


def test_delete_disconnects(client, db, monkeypatch):
    _seed_gmail_cred(db)
    monkeypatch.setattr(agent_router.httpx, "post", lambda *a, **k: types.SimpleNamespace())
    r = client.delete("/agent/email-credentials")
    assert r.status_code == 204
    assert client.get("/agent/email-credentials").json()["connected"] is False


# ── OAuth callback ────────────────────────────────────────────

def test_oauth_callback_stores_refresh_token(client, db, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "secret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://x/api/agent/oauth/callback")

    def fake_post(url, **kwargs):
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"refresh_token": "rt-new",
                          "scope": "https://www.googleapis.com/auth/gmail.modify"},
        )
    monkeypatch.setattr(agent_router.httpx, "post", fake_post)

    state = create_oauth_state(str(TEST_USER_ID))
    r = client.get(f"/agent/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?gmail=connected"

    cred = db.query(models.EmailCredential).filter_by(user_id=TEST_USER_ID).first()
    assert cred is not None
    assert json.loads(decrypt_blob(cred))["refresh_token"] == "rt-new"
    assert cred.enabled is False  # not enabled until labels are set + verified


def test_oauth_callback_bad_state_redirects_error(client):
    r = client.get("/agent/oauth/callback?code=abc&state=garbage", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?gmail=error"


def decrypt_blob(cred):
    from app.security import decrypt_api_key
    return decrypt_api_key(cred.encrypted_blob)


# ── /agent/config injection ───────────────────────────────────

def test_config_injects_shared_client_creds(db, test_user, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "google_oauth_client_id", "shared-cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "shared-secret")
    _seed_gmail_cred(db, refresh_token="rt-xyz", folders=True)

    cfg = get_agent_config(request=types.SimpleNamespace(client=None), db=db, user=test_user)

    blob = cfg.email_credentials
    assert blob["refresh_token"] == "rt-xyz"          # per-user secret
    assert blob["client_id"] == "shared-cid"          # injected, not stored
    assert blob["client_secret"] == "shared-secret"
    assert blob["token_uri"] == "https://oauth2.googleapis.com/token"
    assert cfg.provider == "gmail"
    assert cfg.enabled is True
    assert cfg.folders.root == "Hire Duane"
