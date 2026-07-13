"""Global app settings (feature flags): /admin/settings + the email-agent gates.

The conftest seeds email_agent_enabled=True as the test baseline (production
default is False); these tests exercise the default, the toggle, and the
server-side gates in the disabled state.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import feature_flags, models
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.main import app
from app.security import encrypt_api_key, hash_password

from .conftest import INTERNAL_TOKEN, TEST_USER_ID

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-00000000000a")


@pytest.fixture
def admin_client(db, test_user):
    """Client authenticated as an is_admin user (get_current_admin passes)."""
    admin = models.User(
        id=ADMIN_ID,
        email="admin@example.com",
        password_hash=hash_password("adminpassword"),
        full_name="Admin",
        is_approved=True,
        is_admin=True,
    )
    db.add(admin)
    db.commit()

    def override_get_db():
        yield db

    def override_get_current_user():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        c.headers.update({"X-Internal-Token": INTERNAL_TOKEN})
        yield c
    app.dependency_overrides.clear()


def _clear_flag_row(db):
    """Remove the conftest-seeded row so the code-level default applies."""
    db.query(models.AppSetting).filter(
        models.AppSetting.key == feature_flags.EMAIL_AGENT_ENABLED
    ).delete()
    db.commit()


# ── /admin/settings ───────────────────────────────────────────

def test_settings_default_off_when_unset(admin_client, db):
    _clear_flag_row(db)
    r = admin_client.get("/admin/settings")
    assert r.status_code == 200
    assert r.json() == {"email_agent_enabled": False}


def test_settings_baseline_on_in_tests(admin_client):
    assert admin_client.get("/admin/settings").json()["email_agent_enabled"] is True


def test_settings_put_flips_flag(admin_client):
    r = admin_client.put("/admin/settings", json={"email_agent_enabled": False})
    assert r.status_code == 200
    assert r.json()["email_agent_enabled"] is False
    assert admin_client.get("/admin/settings").json()["email_agent_enabled"] is False

    r = admin_client.put("/admin/settings", json={"email_agent_enabled": True})
    assert r.json()["email_agent_enabled"] is True


def test_settings_put_partial_noop(admin_client):
    """Empty payload changes nothing."""
    before = admin_client.get("/admin/settings").json()
    r = admin_client.put("/admin/settings", json={})
    assert r.status_code == 200
    assert r.json() == before


def test_settings_require_admin(client):
    """The regular (non-admin) client gets 403 on both verbs."""
    assert client.get("/admin/settings").status_code == 403
    assert client.put("/admin/settings", json={"email_agent_enabled": True}).status_code == 403


# ── flag delivery on /auth/me ─────────────────────────────────

def test_auth_me_reflects_flag(client, db):
    assert client.get("/auth/me").json()["email_agent_enabled"] is True
    feature_flags.set_email_agent_enabled(db, False)
    assert client.get("/auth/me").json()["email_agent_enabled"] is False


# ── server-side gates when disabled ───────────────────────────

def _seed_cred(db, user_id):
    db.add(models.EmailCredential(
        user_id=user_id, provider=models.EmailProvider.GMAIL, enabled=True,
        encrypted_blob=encrypt_api_key(json.dumps({"refresh_token": "rt", "scopes": []})),
    ))
    db.commit()


def test_cloud_users_empty_when_disabled(client, db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", INTERNAL_TOKEN)
    _seed_cred(db, TEST_USER_ID)
    # Enabled: the user is listed.
    assert client.get("/agent/cloud/users").json() != []
    # Disabled: enumeration is empty → the cloud CronJob becomes a no-op.
    feature_flags.set_email_agent_enabled(db, False)
    assert client.get("/agent/cloud/users").json() == []


def test_cloud_config_403_when_disabled(client, db, monkeypatch):
    monkeypatch.setattr(settings, "agent_internal_token", INTERNAL_TOKEN)
    _seed_cred(db, TEST_USER_ID)
    feature_flags.set_email_agent_enabled(db, False)
    r = client.get(f"/agent/cloud/config/{TEST_USER_ID}")
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


def test_agent_writer_403_when_disabled(client, db, monkeypatch):
    """get_agent_writer-guarded endpoints refuse all writes when disabled."""
    monkeypatch.setattr(settings, "agent_internal_token", INTERNAL_TOKEN)
    feature_flags.set_email_agent_enabled(db, False)
    r = client.get("/agent/reviews", headers={"X-User-Id": str(TEST_USER_ID)})
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()
