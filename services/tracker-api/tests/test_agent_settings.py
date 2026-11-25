"""GET /agent/settings (INTEGRATION_SPEC §2.4): one settings document for local
and cloud agents, edited through /agent/user-settings.

What these pin:
  • only what the user set is sent: an unset field is omitted, so the agent
    falls back to its .env (that is how a local agent moves off .env gradually);
  • the response never carries a secret [H6a] or an operator guard [H4];
  • validation happens on save, server-side;
  • the old email-policy / retention endpoints and the cloud config read and
    write the same row, so there is one source of truth.
"""

import json
import uuid

import pytest
from cryptography.fernet import Fernet

from app import models
from app.config import settings
from app.security import encrypt_api_key, generate_agent_key

from .conftest import TEST_USER_ID

AGENT = {"X-User-Id": str(TEST_USER_ID)}   # + the client's default X-Internal-Token


def _get(client, headers=AGENT):
    r = client.get("/agent/settings", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _patch(client, body, expect=200):
    r = client.patch("/agent/user-settings", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def _criteria(db, **kw):
    db.add(models.Criteria(user_id=TEST_USER_ID, name="default", is_active=True, **kw))
    db.commit()


# ── shape ──────────────────────────────────────────────────────

def test_nothing_set_is_an_empty_document(client):
    assert _get(client) == {}


def test_only_set_fields_are_sent(client):
    _patch(client, {"sorter_enabled": True, "dedup_window_days": 21, "retention_social_days": 14,
                    "staffing_enabled": True, "staffing_draft_score_threshold": 8.5})
    assert _get(client) == {
        "sorter": {"enabled": True},
        "email_policy": {"dedup_window_days": 21},
        "retention": {"social_days": 14},
        "staffing": {"enabled": True, "draft_score_threshold": 8.5},
    }


def test_false_and_empty_list_are_real_settings(client):
    """[] allowed senders means "allow any" and False means off — neither is "unset"."""
    _patch(client, {"allowed_sender_domains": [], "require_sender_auth": False,
                    "staffing_auto_draft": False})
    doc = _get(client)
    assert doc["email_policy"] == {"allowed_sender_domains": [], "require_sender_auth": False}
    assert doc["staffing"] == {"auto_draft": False}


def test_full_document_matches_the_spec_shape(client, db):
    _criteria(db, work_style="remote", home_city="Austin, TX", search_locations=["TX"],
              min_salary=150000, excluded_companies=["Initech"])
    _patch(client, {
        "sorter_enabled": True, "sorter_min_confidence": 0.85, "sorter_min_margin": 0.3,
        "bulk_recruiter_domains": ["Mail.Example.com"],
        "allowed_sender_domains": ["linkedin.com"], "require_sender_auth": True,
        "zero_postings_action": "mark_read", "dedup_window_days": 30, "max_email_age_days": 14,
        "retention_social_days": 14, "retention_postings_days": 14,
        "staffing_enabled": True, "staffing_auto_draft": True,
        "staffing_excluded_sender_domains": ["linkedin.com"],
        "staffing_allowed_employment_types": ["full_time", "contract_c2c"],
        "staffing_draft_score_threshold": 8.5, "staffing_dedup_window_days": 30,
        "staffing_mark_read": {"recruiter_outreach": True, "direct_message": False},
        "staffing_draft_prompt": "  Keep it short.  ", "staffing_slack_notify": True,
    })
    assert _get(client) == {
        "sorter": {"enabled": True, "min_confidence": 0.85, "min_margin": 0.3,
                   "bulk_recruiter_domains": ["mail.example.com"]},
        "email_policy": {"allowed_sender_domains": ["linkedin.com"], "require_sender_auth": True,
                         "zero_postings_action": "mark_read", "dedup_window_days": 30,
                         "max_email_age_days": 14},
        "retention": {"social_days": 14, "postings_days": 14},
        "staffing": {
            "enabled": True, "auto_draft": True,
            "excluded_sender_domains": ["linkedin.com"],
            "allowed_employment_types": ["full_time", "contract_c2c"],
            "draft_score_threshold": 8.5, "dedup_window_days": 30,
            "mark_read": {"recruiter_outreach": True, "direct_message": False},
            "draft_prompt": "Keep it short.", "slack_notify": True,
            "criteria": {"work_style": "remote", "home_city": "Austin, TX",
                         "search_locations": ["TX"], "min_salary": 150000,
                         "excluded_companies": ["Initech"]},
        },
    }


def test_criteria_are_read_live(client, db):
    """Copied from the active Criteria row at read time, so they can't drift."""
    _criteria(db, work_style="hybrid", home_city="Denver, CO")
    assert _get(client)["staffing"]["criteria"] == {"work_style": "hybrid", "home_city": "Denver, CO"}
    crit = db.query(models.Criteria).one()
    crit.work_style = "onsite"
    db.commit()
    assert _get(client)["staffing"]["criteria"]["work_style"] == "onsite"


def test_writing_skills_only_enabled_and_scoped_email_draft(client, db):
    _criteria(db, writing_skills=[
        {"id": "a", "name": "A", "content": "DRAFT RULES", "enabled": True, "scopes": ["email_draft"]},
        {"id": "b", "name": "B", "content": "OFF", "enabled": False, "scopes": ["email_draft"]},
        {"id": "c", "name": "C", "content": "APPLICATION ONLY", "enabled": True, "scopes": ["application"]},
    ])
    assert _get(client)["staffing"]["writing_skills"] == ["DRAFT RULES"]


def test_email_draft_is_an_accepted_skill_scope(client):
    r = client.put("/criteria", json={"name": "default", "writing_skills": [
        {"id": "a", "name": "A", "content": "x", "enabled": True, "scopes": ["email_draft"]}]})
    assert r.status_code in (200, 201), r.text


# ── no secrets, no operator guards ─────────────────────────────

FORBIDDEN_KEYS = {
    # secrets [H6a]
    "api_key", "key", "password", "token", "bot_token", "refresh_token", "encrypted_blob",
    "email_credentials", "llm", "slack", "typesafe_api_key", "langfuse_secret_key",
    # operator guards [H4]
    "daily_spend_ceiling_usd", "emails_per_run", "max_emails_per_run", "retention_max_per_run",
    "poll_interval", "budget",
}


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


def test_never_carries_a_secret_or_an_operator_guard(client, db, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    db.add(models.UserAPIKey(user_id=TEST_USER_ID, provider=models.LLMProvider.ANTHROPIC,
                             encrypted_key=encrypt_api_key("sk-SECRET-llm"), preferred_model="m"))
    db.add(models.EmailCredential(user_id=TEST_USER_ID, provider=models.EmailProvider.GMAIL, enabled=True,
                                  encrypted_blob=encrypt_api_key(json.dumps({"refresh_token": "rt-SECRET"}))))
    db.add(models.SlackConnection(user_id=TEST_USER_ID, encrypted_bot_token=encrypt_api_key("xoxb-SECRET"),
                                  channel_id="C1"))
    _criteria(db, work_style="remote")
    _patch(client, {"sorter_enabled": True, "staffing_enabled": True, "retention_social_days": 7})

    doc = _get(client)
    text = json.dumps(doc)
    assert "SECRET" not in text
    assert not FORBIDDEN_KEYS & set(_keys(doc)), set(_keys(doc))


def test_an_operator_guard_cannot_be_saved(client):
    """A user must not be able to raise their own spend [H4]: unknown fields are refused."""
    for field in ("daily_spend_ceiling_usd", "max_emails_per_run", "retention_max_per_run"):
        _patch(client, {field: 999}, expect=422)


# ── validation ─────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"allowed_sender_domains": ["https://evil.example/path"]},
    {"bulk_recruiter_domains": ["not a domain"]},
    {"staffing_excluded_sender_domains": ["exa mple.com"]},
    {"dedup_window_days": 0},
    {"max_email_age_days": 366},
    {"staffing_dedup_window_days": -1},
    {"retention_postings_days": 400},
    {"staffing_draft_score_threshold": 10.5},
    {"staffing_draft_score_threshold": -1},
    {"sorter_min_confidence": 1.5},
    {"sorter_min_margin": -0.1},
    {"zero_postings_action": "delete"},
    {"staffing_allowed_employment_types": ["full_time", "gig"]},
    {"staffing_mark_read": {"job_alert": True}},
    {"staffing_mark_read": {"recruiter_outreach": "yes"}},
    {"staffing_draft_prompt": "x" * 10_001},
])
def test_bad_values_are_refused_on_save(client, body):
    _patch(client, body, expect=422)


def test_retention_zero_means_off(client):
    assert _patch(client, {"retention_social_days": 0})["retention_social_days"] is None


# ── partial update ─────────────────────────────────────────────

def test_patch_writes_only_the_fields_sent_and_null_clears(client):
    _patch(client, {"sorter_enabled": True, "staffing_enabled": True})
    out = _patch(client, {"staffing_enabled": None})
    assert out["sorter_enabled"] is True
    assert out["staffing_enabled"] is None
    assert _get(client) == {"sorter": {"enabled": True}}
    assert client.get("/agent/user-settings").json()["sorter_enabled"] is True


def test_works_with_no_mailbox_connected(client, db):
    """A local agent keeps its mail login in .env — there is no EmailCredential."""
    assert db.query(models.EmailCredential).count() == 0
    _patch(client, {"staffing_enabled": True})
    assert _get(client) == {"staffing": {"enabled": True}}


# ── auth ───────────────────────────────────────────────────────

def test_agent_key_path(client, db):
    raw, key_hash, hint = generate_agent_key()
    db.add(models.AgentAPIKey(id=uuid.uuid4(), user_id=TEST_USER_ID,
                              key_hash=key_hash, key_hint=hint, revoked=False))
    db.commit()
    _patch(client, {"staffing_enabled": True})
    client.headers.pop("X-Internal-Token", None)
    assert _get(client, headers={"X-Agent-Key": raw}) == {"staffing": {"enabled": True}}


def test_needs_an_agent_credential(client):
    client.headers.pop("X-Internal-Token", None)
    assert client.get("/agent/settings").status_code == 401


def test_settings_belong_to_the_caller(client, db):
    """Another user's settings never leak into this user's document [H1]."""
    other = models.User(id=uuid.uuid4(), email="o@example.com", password_hash="x", is_approved=True)
    db.add(other)
    db.add(models.AgentSettings(user_id=other.id, staffing_enabled=True, sorter_enabled=True))
    db.commit()
    assert _get(client) == {}


# ── one source of truth with the older endpoints ───────────────

@pytest.fixture
def mailbox(db, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    db.add(models.EmailCredential(user_id=TEST_USER_ID, provider=models.EmailProvider.GMAIL, enabled=True,
                                  encrypted_blob=encrypt_api_key(json.dumps({"refresh_token": "rt"}))))
    db.commit()


def test_old_policy_and_retention_endpoints_write_the_same_row(client, db, mailbox):
    r = client.put("/agent/email-credentials/policy",
                   json={"allowed_sender_domains": ["dice.com"], "require_sender_auth": False})
    assert r.status_code == 200, r.text
    assert r.json()["email_policy"]["allowed_sender_domains"] == ["dice.com"]
    r = client.put("/agent/email-credentials/retention", json={"social_days": 10, "postings_days": 0})
    assert r.status_code == 200, r.text
    assert r.json()["retention"] == {"social_days": 10, "postings_days": None}

    doc = _get(client)
    assert doc["email_policy"] == {"allowed_sender_domains": ["dice.com"], "require_sender_auth": False}
    assert doc["retention"] == {"social_days": 10}

    cloud = client.get(f"/agent/cloud/config/{TEST_USER_ID}").json()
    assert cloud["email_policy"]["allowed_sender_domains"] == ["dice.com"]
    assert cloud["retention"]["social_days"] == 10


def test_new_settings_reach_the_cloud_config(client, mailbox):
    _patch(client, {"allowed_sender_domains": ["indeed.com"], "retention_postings_days": 5})
    cloud = client.get(f"/agent/cloud/config/{TEST_USER_ID}").json()
    assert cloud["email_policy"]["allowed_sender_domains"] == ["indeed.com"]
    assert cloud["retention"]["postings_days"] == 5


def test_settings_survive_disconnecting_the_mailbox(client, db, mailbox):
    _patch(client, {"staffing_enabled": True})
    db.query(models.EmailCredential).delete()
    db.commit()
    assert _get(client) == {"staffing": {"enabled": True}}


def test_settings_are_deleted_with_the_user(db, test_user):
    db.add(models.AgentSettings(user_id=TEST_USER_ID, staffing_enabled=True))
    db.commit()
    db.delete(test_user)
    db.commit()
    assert db.query(models.AgentSettings).count() == 0
