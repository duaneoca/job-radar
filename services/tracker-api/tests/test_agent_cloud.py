"""JR-5 cloud multi-user: /agent/cloud/* enumeration (internal-token) and the
dual-mode get_agent_writer auth (X-Agent-Key OR X-Internal-Token + user_id)."""

import json
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app import models
from app.config import settings
from app.deps import get_agent_writer, require_internal_token
from app.security import encrypt_api_key, generate_agent_key, hash_password

from .conftest import TEST_USER_ID

TOKEN = "internal-token-abc"


def _seed_cred(db, user_id, *, enabled=True, provider=models.EmailProvider.GMAIL,
               refresh_token="rt-1"):
    db.add(models.EmailCredential(
        user_id=user_id, provider=provider, enabled=enabled,
        encrypted_blob=encrypt_api_key(json.dumps({
            "refresh_token": refresh_token,
            "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        })),
    ))
    db.commit()


def _make_user(db, approved=True):
    uid = uuid.uuid4()
    db.add(models.User(
        id=uid, email=f"{uid}@example.com", password_hash=hash_password("x"),
        full_name="U", is_approved=approved, is_admin=False,
    ))
    db.commit()
    return uid


# ── require_internal_token (in-cluster gate) ──────────────────

def test_internal_token_unset_rejects_all(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", "")
    with pytest.raises(HTTPException) as e:
        require_internal_token(x_internal_token=TOKEN)
    assert e.value.status_code == 401


def test_internal_token_wrong_rejected(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    with pytest.raises(HTTPException):
        require_internal_token(x_internal_token="nope")


def test_internal_token_correct_passes(monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    assert require_internal_token(x_internal_token=TOKEN) is None


# ── GET /agent/cloud/users ────────────────────────────────────

def test_cloud_users_requires_token(client):
    assert client.get("/agent/cloud/users").status_code == 401


def test_cloud_users_lists_enabled_with_creds(client, db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    _seed_cred(db, TEST_USER_ID, enabled=True)
    other = _make_user(db)
    _seed_cred(db, other, enabled=False)  # disabled → excluded

    r = client.get("/agent/cloud/users", headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    ids = {u["user_id"] for u in body}
    assert str(TEST_USER_ID) in ids
    assert str(other) not in ids
    assert all("refresh_token" not in json.dumps(u) for u in body)  # no secrets


# ── GET /agent/cloud/config/{user_id} ─────────────────────────

def test_cloud_config_returns_injected_blob(client, db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "google_oauth_client_id", "shared-cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "shared-secret")
    _seed_cred(db, TEST_USER_ID, refresh_token="rt-xyz")

    r = client.get(f"/agent/cloud/config/{TEST_USER_ID}", headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200
    blob = r.json()["email_credentials"]
    assert blob["refresh_token"] == "rt-xyz"
    assert blob["client_id"] == "shared-cid"
    assert r.json()["enabled"] is True


def test_cloud_config_unknown_user_404(client, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    r = client.get(f"/agent/cloud/config/{uuid.uuid4()}", headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 404


# ── GET /agent/reviews is per-user operational → dual-auth ────

def test_reviews_accepts_internal_token(client, monkeypatch):
    """Per the §2.1b invariant: per-user operational endpoints take either auth mode."""
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    r = client.get("/agent/reviews",
                   headers={"X-Internal-Token": TOKEN, "X-Agent-User-Id": str(TEST_USER_ID)})
    assert r.status_code == 200
    assert r.json() == []  # test_user has no reviews


def test_reviews_rejects_no_auth(client):
    assert client.get("/agent/reviews").status_code == 401


# ── get_agent_writer dual-mode ────────────────────────────────

def test_writer_agent_key_path(db, test_user):
    raw, key_hash, hint = generate_agent_key()
    db.add(models.AgentAPIKey(id=uuid.uuid4(), user_id=TEST_USER_ID,
                              key_hash=key_hash, key_hint=hint, revoked=False))
    db.commit()
    u = get_agent_writer(x_agent_key=raw, x_internal_token=None, x_agent_user_id=None, db=db)
    assert u.id == TEST_USER_ID


def test_writer_internal_path(db, test_user, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    u = get_agent_writer(x_agent_key=None, x_internal_token=TOKEN,
                         x_agent_user_id=str(TEST_USER_ID), db=db)
    assert u.id == TEST_USER_ID


def test_writer_internal_without_user_id_400(db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    with pytest.raises(HTTPException) as e:
        get_agent_writer(x_agent_key=None, x_internal_token=TOKEN, x_agent_user_id=None, db=db)
    assert e.value.status_code == 400


def test_writer_internal_bad_token_401(db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", TOKEN)
    with pytest.raises(HTTPException) as e:
        get_agent_writer(x_agent_key=None, x_internal_token="wrong",
                         x_agent_user_id=str(TEST_USER_ID), db=db)
    assert e.value.status_code == 401


def test_writer_no_auth_401(db):
    with pytest.raises(HTTPException) as e:
        get_agent_writer(x_agent_key=None, x_internal_token=None, x_agent_user_id=None, db=db)
    assert e.value.status_code == 401
