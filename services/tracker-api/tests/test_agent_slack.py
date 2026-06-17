"""Per-user Slack OAuth install (JR-6): install URL, callback token storage,
channel selection, and /agent/config injection."""

import types

from cryptography.fernet import Fernet

from app import models
from app.config import settings
from app.routers import agent as agent_router
from app.routers.agent import get_agent_config
from app.security import create_oauth_state, decrypt_api_key, encrypt_api_key

from .conftest import TEST_USER_ID


def _seed_conn(db, *, channel_id=None, token="xoxb-test"):
    db.add(models.SlackConnection(
        user_id=TEST_USER_ID, encrypted_bot_token=encrypt_api_key(token),
        team_id="T1", team_name="Acme", bot_user_id="B1", scopes="chat:write",
        channel_id=channel_id, channel_name="general" if channel_id else None,
    ))
    db.commit()


# ── install URL ───────────────────────────────────────────────

def test_start_503_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "")
    assert client.get("/agent/slack/oauth/start").status_code == 503


def test_start_returns_authorize_url(client, monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "cid")
    monkeypatch.setattr(settings, "slack_oauth_redirect_uri", "https://x/api/agent/slack/oauth/callback")
    url = client.get("/agent/slack/oauth/start").json()["authorization_url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=cid" in url and "state=" in url


# ── callback ──────────────────────────────────────────────────

def test_callback_stores_token(client, db, monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "cid")
    monkeypatch.setattr(settings, "slack_client_secret", "secret")
    monkeypatch.setattr(settings, "slack_oauth_redirect_uri", "https://x/api/agent/slack/oauth/callback")
    monkeypatch.setattr(agent_router.httpx, "post", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"ok": True, "access_token": "xoxb-new",
                      "team": {"id": "T9", "name": "Acme"}, "bot_user_id": "B9",
                      "scope": "chat:write"},
    ))
    state = create_oauth_state(str(TEST_USER_ID), purpose="slack-oauth")
    r = client.get(f"/agent/slack/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?slack=connected"
    conn = db.query(models.SlackConnection).filter_by(user_id=TEST_USER_ID).first()
    assert decrypt_api_key(conn.encrypted_bot_token) == "xoxb-new"
    assert conn.team_name == "Acme"


def test_callback_rejects_gmail_state(client):
    # A gmail-purpose state must not authorize a slack install (purpose namespacing).
    state = create_oauth_state(str(TEST_USER_ID))  # default purpose = gmail-oauth
    r = client.get(f"/agent/slack/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/settings?slack=error"


def test_callback_not_ok_redirects_error(client, monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "cid")
    monkeypatch.setattr(settings, "slack_client_secret", "secret")
    monkeypatch.setattr(settings, "slack_oauth_redirect_uri", "https://x/cb")
    monkeypatch.setattr(agent_router.httpx, "post", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"ok": False, "error": "bad_code"}))
    state = create_oauth_state(str(TEST_USER_ID), purpose="slack-oauth")
    r = client.get(f"/agent/slack/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/settings?slack=error"


# ── status / channels / channel / disconnect ──────────────────

def test_status_not_connected(client):
    body = client.get("/agent/slack/status").json()
    assert body["connected"] is False


def test_status_connected(client, db):
    _seed_conn(db)
    body = client.get("/agent/slack/status").json()
    assert body["connected"] is True and body["team_name"] == "Acme"
    assert "xoxb" not in __import__("json").dumps(body)  # token never leaks


def test_channels_lists(client, db, monkeypatch):
    _seed_conn(db)
    monkeypatch.setattr(agent_router.httpx, "get", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"ok": True, "channels": [{"id": "C1", "name": "general"},
                                               {"id": "C2", "name": "jobs"}]}))
    body = client.get("/agent/slack/channels").json()
    assert {c["name"] for c in body} == {"general", "jobs"}


def test_channels_404_when_not_connected(client):
    assert client.get("/agent/slack/channels").status_code == 404


def test_set_channel(client, db):
    _seed_conn(db)
    r = client.put("/agent/slack/channel", json={"channel_id": "C1", "channel_name": "general"})
    assert r.status_code == 200
    assert r.json()["channel_id"] == "C1"


def test_set_channel_404_when_not_connected(client):
    assert client.put("/agent/slack/channel", json={"channel_id": "C1"}).status_code == 404


def test_disconnect(client, db, monkeypatch):
    _seed_conn(db, channel_id="C1")
    monkeypatch.setattr(agent_router.httpx, "post", lambda *a, **k: types.SimpleNamespace())
    assert client.delete("/agent/slack").status_code == 204
    assert client.get("/agent/slack/status").json()["connected"] is False


# ── /agent/config injection ───────────────────────────────────

def test_config_includes_slack_when_channel_set(db, test_user, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    _seed_conn(db, channel_id="C1", token="xoxb-live")
    cfg = get_agent_config(request=types.SimpleNamespace(client=None), db=db, user=test_user)
    assert cfg.slack is not None
    assert cfg.slack.bot_token == "xoxb-live"
    assert cfg.slack.channel_id == "C1"


def test_config_no_slack_without_channel(db, test_user, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    _seed_conn(db, channel_id=None)   # connected but no channel chosen yet
    cfg = get_agent_config(request=types.SimpleNamespace(client=None), db=db, user=test_user)
    assert cfg.slack is None
