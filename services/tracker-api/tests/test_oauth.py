"""The OAuth authorization server behind Settings → Connected apps.

These run the REAL get_current_user (cookie for the consent page, bearer for
the API), so they use their own client fixture without the auth override.
"""

import base64
import hashlib
import secrets
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import models, oauth
from app.database import get_db
from app.main import app

from .conftest import INTERNAL_TOKEN, TEST_USER_ID

CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
CHATGPT_CB = "https://chatgpt.com/connector_platform_oauth_redirect"
GROK_CB = "https://grok.com/connectors-oauth-exchange-code/"
GEMINI_CB = "https://oauth-redirect.googleusercontent.com/r/user_bound_custom-mcp-abc123-job-radar.net"
BASE = "http://testserver"


@pytest.fixture
def web(db, test_user):
    """A browser: cookie session from a real login, no dependency override."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers.update({"X-Internal-Token": INTERNAL_TOKEN})
        r = c.post("/auth/login", json={"email": "test@example.com", "password": "testpassword"})
        assert r.status_code == 200, r.text
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def api(db):
    """An assistant's server: no cookie, bearer tokens only."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_ratelimit(monkeypatch):
    from app import ratelimit
    monkeypatch.setattr(ratelimit, "_failures", {})


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(api, redirect_uris=(CLAUDE_CB,), name="Claude", headers=None, **extra):
    r = api.post("/oauth/register", json={"redirect_uris": list(redirect_uris), "client_name": name,
                                          "token_endpoint_auth_method": "none", **extra},
                 headers=headers or {})
    return r


def _authorize_params(client_id, challenge, redirect_uri=CLAUDE_CB, scope="jobs:read jobs:write generate", **over):
    p = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
         "code_challenge": challenge, "code_challenge_method": "S256", "scope": scope, "state": "xyz"}
    p.update(over)
    return p


def _consent(web, api, scope="jobs:read jobs:write generate", redirect_uri=CLAUDE_CB, name="Claude"):
    """Register → consent (allow) → code → tokens. Returns (tokens, client_id, verifier)."""
    client_id = _register(api, (redirect_uri,), name=name).json()["client_id"]
    verifier, challenge = _pkce()
    params = _authorize_params(client_id, challenge, redirect_uri=redirect_uri, scope=scope)
    assert web.get("/oauth/authorize/context", params=params).status_code == 200
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "allow"})
    assert r.status_code == 200, r.text
    q = parse_qs(urlsplit(r.json()["redirect_to"]).query)
    assert q["state"] == ["xyz"] and q["iss"] == [BASE]
    t = api.post("/oauth/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                       "redirect_uri": redirect_uri, "client_id": client_id,
                                       "code_verifier": verifier})
    assert t.status_code == 200, t.text
    return t.json(), client_id, verifier


def _bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _job(api_or_web):
    with patch("app.routers.jobs._celery"):
        api_or_web.post(f"/jobs?user_id={TEST_USER_ID}", headers={"X-Internal-Token": INTERNAL_TOKEN},
                        json={"title": "Eng", "company": "Acme", "url": "https://x/1", "source": "manual"})


# ── discovery ─────────────────────────────────────────────────

def test_metadata_advertises_what_the_assistants_check(api):
    for path in ("/.well-known/oauth-authorization-server", "/oauth/metadata"):
        m = api.get(path).json()
        assert m["issuer"] == BASE
        assert m["authorization_endpoint"] == f"{BASE}/oauth/authorize"
        assert m["token_endpoint"] == f"{BASE}/api/oauth/token"
        assert m["registration_endpoint"] == f"{BASE}/api/oauth/register"
        assert m["code_challenge_methods_supported"] == ["S256"]
        # Claude picks CIMD only when both of these are present.
        assert m["client_id_metadata_document_supported"] is True
        assert m["token_endpoint_auth_methods_supported"] == ["none"]
        # ChatGPT uses its stable callback only when the issuer is echoed.
        assert m["authorization_response_iss_parameter_supported"] is True
        assert set(m["scopes_supported"]) == set(models.OAUTH_SCOPES)


# ── registration: the allowlist is the whole admission control ─

@pytest.mark.parametrize("uri,vendor", [
    (CLAUDE_CB, "anthropic"), (CHATGPT_CB, "openai"), (GROK_CB, "xai"), (GEMINI_CB, "google"),
    ("https://chatgpt.com/connector/oauth/cb_9f8e7d", "openai"),
])
def test_each_vendor_callback_registers(api, db, uri, vendor):
    r = _register(api, (uri,))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["redirect_uris"] == [uri] and body["token_endpoint_auth_method"] == "none"
    assert db.get(models.OAuthClient, body["client_id"]).vendor == vendor


@pytest.mark.parametrize("uri", [
    "http://localhost:3118/callback",            # developer tools: any local process can bind the port
    "http://127.0.0.1/callback",
    "https://evil.example.com/callback",
    "https://claude.ai/api/mcp/auth_callback/../x",
    "https://claude.ai.evil.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector/oauth/",       # per-connection id missing
    "https://oauth-redirect.googleusercontent.com/r/something-else",
    "http://claude.ai/api/mcp/auth_callback",     # not https
])
def test_other_redirects_are_refused(api, db, uri):
    r = _register(api, (uri,))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"
    assert db.query(models.OAuthClient).count() == 0


def test_mixed_vendors_in_one_client_are_refused(api):
    assert _register(api, (CLAUDE_CB, CHATGPT_CB)).json()["error"] == "invalid_redirect_uri"


def test_confidential_clients_are_refused(api):
    r = _register(api, token_endpoint_auth_method="client_secret_post")
    assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"


def test_registration_is_rate_limited(api):
    for _ in range(10):
        assert _register(api).status_code == 201
    assert _register(api).status_code == 429


def test_a_forged_x_forwarded_for_does_not_reset_the_limiter(api):
    """nginx appends to X-Forwarded-For, so its first element is the client's
    to choose. It must never be the key."""
    for i in range(10):
        assert _register(api, headers={"X-Forwarded-For": f"10.0.0.{i}, 1.2.3.4"}).status_code == 201
    assert _register(api, headers={"X-Forwarded-For": "10.0.0.99, 1.2.3.4"}).status_code == 429


def test_cloudflares_header_is_the_key(api):
    for _ in range(10):
        assert _register(api, headers={"CF-Connecting-IP": "203.0.113.5"}).status_code == 201
    assert _register(api, headers={"CF-Connecting-IP": "203.0.113.5"}).status_code == 429
    assert _register(api, headers={"CF-Connecting-IP": "203.0.113.6"}).status_code == 201


def test_registrations_have_a_global_ceiling(api, monkeypatch):
    from app.routers import oauth as oauth_router
    monkeypatch.setattr(oauth_router, "GLOBAL_REGISTER_LIMIT", 3)
    for i in range(3):
        assert _register(api, headers={"CF-Connecting-IP": f"198.51.100.{i}"}).status_code == 201
    assert _register(api, headers={"CF-Connecting-IP": "198.51.100.200"}).status_code == 429


def test_limiter_table_is_bounded():
    from app import ratelimit
    for i in range(ratelimit._MAX_KEYS + 2_000):
        ratelimit.record_failure(f"spray:{i}")
    assert len(ratelimit._failures) <= ratelimit._MAX_KEYS


def test_unused_registrations_are_swept_and_granted_ones_kept(web, api, db):
    from app.routers.admin import _do_cleanup
    unused = _register(api).json()["client_id"]
    _consent(web, api)                                  # a client with a grant
    for c in db.query(models.OAuthClient):
        c.created_at = oauth._utcnow() - timedelta(days=2)
    db.commit()
    assert _do_cleanup(db)["oauth_clients_deleted"] == 1
    assert db.get(models.OAuthClient, unused) is None
    assert db.query(models.OAuthClient).count() == 1


# ── CIMD ──────────────────────────────────────────────────────

CIMD_URL = "https://claude.ai/oauth/hosted-client-metadata"


def _cimd_doc(**over):
    doc = {"client_id": CIMD_URL, "client_name": "Claude", "redirect_uris": [CLAUDE_CB],
           "token_endpoint_auth_method": "none"}
    doc.update(over)
    return doc


def test_cimd_client_is_fetched_validated_and_cached(web, db, monkeypatch):
    calls = []
    monkeypatch.setattr(oauth, "_fetch_cimd", lambda url: (calls.append(url), _cimd_doc())[1])
    _, challenge = _pkce()
    params = _authorize_params(CIMD_URL, challenge)
    ctx = web.get("/oauth/authorize/context", params=params)
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["vendor"] == "anthropic" and ctx.json()["client_kind"] == "cimd"
    assert ctx.json()["redirect_host"] == "claude.ai"
    web.get("/oauth/authorize/context", params=params)
    assert calls == [CIMD_URL]                        # second read came from the cache
    row = db.get(models.OAuthClient, CIMD_URL)
    assert row.kind == "cimd" and row.redirect_uris == [CLAUDE_CB]


def test_cimd_refetches_after_a_day(web, db, monkeypatch):
    calls = []
    monkeypatch.setattr(oauth, "_fetch_cimd", lambda url: (calls.append(url), _cimd_doc())[1])
    _, challenge = _pkce()
    params = _authorize_params(CIMD_URL, challenge)
    web.get("/oauth/authorize/context", params=params)
    row = db.get(models.OAuthClient, CIMD_URL)
    row.metadata_fetched_at = oauth._utcnow() - oauth.CIMD_REFETCH_AFTER - timedelta(minutes=1)
    db.commit()
    web.get("/oauth/authorize/context", params=params)
    assert len(calls) == 2


@pytest.mark.parametrize("url", [
    "https://evil.example.com/metadata.json",
    "https://10.0.0.5/metadata.json",
    "https://claude.ai.evil.com/metadata.json",
    "http://claude.ai/oauth/metadata",
    "https://commondatastorage.googleusercontent.com/bucket/metadata.json",   # user-upload host
    "https://sites.google.com/view/anything/metadata.json",
])
def test_cimd_from_a_non_vendor_host_is_never_fetched(web, monkeypatch, url):
    monkeypatch.setattr(oauth, "_fetch_cimd", lambda u: pytest.fail("must not fetch " + u))
    _, challenge = _pkce()
    r = web.get("/oauth/authorize/context", params=_authorize_params(url, challenge))
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_client"
    assert r.json()["detail"]["redirect_to"] is None


@pytest.mark.parametrize("doc", [
    _cimd_doc(client_id="https://claude.ai/oauth/other"),            # doesn't name itself
    _cimd_doc(redirect_uris=["https://evil.example.com/cb"]),        # redirect off the allowlist
    _cimd_doc(redirect_uris=[CHATGPT_CB]),                           # vendor mismatch with the host
    _cimd_doc(redirect_uris=["http://localhost:1234/cb"]),           # loopback
    _cimd_doc(token_endpoint_auth_method="client_secret_basic"),
    {"not": "a document"},
])
def test_cimd_document_rules(web, db, monkeypatch, doc):
    monkeypatch.setattr(oauth, "_fetch_cimd", lambda url: doc)
    _, challenge = _pkce()
    r = web.get("/oauth/authorize/context", params=_authorize_params(CIMD_URL, challenge))
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_client"
    assert db.get(models.OAuthClient, CIMD_URL) is None


# ── consent page ──────────────────────────────────────────────

def test_consent_context_names_the_vendor_from_the_redirect_not_the_client(web, api):
    client_id = _register(api, name="Totally Legit Bank").json()["client_id"]
    _, challenge = _pkce()
    ctx = web.get("/oauth/authorize/context", params=_authorize_params(client_id, challenge)).json()
    assert ctx["vendor_label"] == "Claude (Anthropic)"
    assert ctx["client_name"] == "Totally Legit Bank"       # shown, but second
    assert ctx["redirect_host"] == "claude.ai"
    assert [s["id"] for s in ctx["scopes"]] == ["jobs:read", "jobs:write", "generate"]


def test_consent_requires_a_signed_in_user(api):
    client_id = _register(api).json()["client_id"]
    _, challenge = _pkce()
    assert api.get("/oauth/authorize/context", params=_authorize_params(client_id, challenge)).status_code == 401


def test_unknown_client_and_unregistered_redirect_never_redirect(web, api):
    _, challenge = _pkce()
    r = web.get("/oauth/authorize/context", params=_authorize_params("jr_client_nope", challenge))
    assert r.status_code == 400 and r.json()["detail"]["redirect_to"] is None
    client_id = _register(api).json()["client_id"]
    r = web.get("/oauth/authorize/context",
                params=_authorize_params(client_id, challenge, redirect_uri="https://evil.example.com/cb"))
    assert r.status_code == 400 and r.json()["detail"]["redirect_to"] is None


@pytest.mark.parametrize("over,error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge": "short"}, "invalid_request"),
    ({"scope": "jobs:read admin:everything"}, "invalid_scope"),
])
def test_protocol_errors_after_redirect_is_verified_go_back_to_the_client(web, api, over, error):
    client_id = _register(api).json()["client_id"]
    _, challenge = _pkce()
    r = web.get("/oauth/authorize/context", params=_authorize_params(client_id, challenge, **over))
    assert r.status_code == 400
    to = r.json()["detail"]["redirect_to"]
    assert to.startswith(CLAUDE_CB + "?")
    q = parse_qs(urlsplit(to).query)
    assert q["error"] == [error] and q["state"] == ["xyz"]


def test_deny_sends_access_denied(web, api):
    client_id = _register(api).json()["client_id"]
    _, challenge = _pkce()
    params = _authorize_params(client_id, challenge)
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "deny"})
    q = parse_qs(urlsplit(r.json()["redirect_to"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["xyz"] and q["iss"] == [BASE]
    assert "code" not in q


def test_empty_scope_means_every_scope(web, api):
    client_id = _register(api).json()["client_id"]
    _, challenge = _pkce()
    ctx = web.get("/oauth/authorize/context", params=_authorize_params(client_id, challenge, scope="")).json()
    assert [s["id"] for s in ctx["scopes"]] == list(models.OAUTH_SCOPES)


# ── code → tokens ─────────────────────────────────────────────

def test_full_flow_then_the_bearer_works_on_the_jobs_surface(web, api, db, monkeypatch):
    _job(api)
    tokens, client_id, _ = _consent(web, api)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert tokens["scope"] == "jobs:read jobs:write generate"
    h = _bearer(tokens)

    me = api.get("/auth/me", headers=h)
    assert me.status_code == 200 and me.json()["email"] == "test@example.com"
    jobs = api.get("/jobs", headers=h).json()
    assert jobs["total"] == 1
    rid = jobs["items"][0]["id"]
    assert api.get(f"/jobs/{rid}", headers=h).status_code == 200
    assert api.get(f"/jobs/{rid}/generation", headers=h).status_code == 200
    assert api.patch(f"/jobs/{rid}", headers=h, json={"starred": True}).json()["starred"] is True

    grant = db.query(models.OAuthGrant).one()
    assert grant.last_used_at is not None
    assert db.query(models.OAuthToken).count() == 2          # both stored hashed
    assert all(len(t.token_hash) == 64 for t in db.query(models.OAuthToken))


def test_tokens_are_never_stored_in_the_clear(web, api, db):
    tokens, _, _ = _consent(web, api)
    stored = {t.token_hash for t in db.query(models.OAuthToken)}
    assert tokens["access_token"] not in stored and tokens["refresh_token"] not in stored
    assert db.query(models.OAuthAuthorizationCode).one().used_at is not None


@pytest.mark.parametrize("tamper", [
    lambda f, cid, v: {**f, "code_verifier": "x" * 50},
    lambda f, cid, v: {**f, "redirect_uri": CHATGPT_CB},
    lambda f, cid, v: {**f, "code": "jr_nope"},
])
def test_code_exchange_rejects_tampering(web, api, tamper):
    client_id = _register(api).json()["client_id"]
    verifier, challenge = _pkce()
    params = _authorize_params(client_id, challenge)
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "allow"})
    code = parse_qs(urlsplit(r.json()["redirect_to"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_CB,
            "client_id": client_id, "code_verifier": verifier}
    t = api.post("/oauth/token", data=tamper(form, client_id, verifier))
    assert t.status_code == 400 and t.json()["error"] == "invalid_grant"


def test_code_for_one_client_cannot_be_redeemed_by_another(web, api):
    a = _register(api).json()["client_id"]
    b = _register(api).json()["client_id"]
    verifier, challenge = _pkce()
    params = _authorize_params(a, challenge)
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "allow"})
    code = parse_qs(urlsplit(r.json()["redirect_to"]).query)["code"][0]
    t = api.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_CB,
                                       "client_id": b, "code_verifier": verifier})
    assert t.json()["error"] == "invalid_grant"


def test_a_code_is_single_use_and_reuse_revokes_the_grant(web, api, db):
    client_id = _register(api).json()["client_id"]
    verifier, challenge = _pkce()
    params = _authorize_params(client_id, challenge)
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "allow"})
    code = parse_qs(urlsplit(r.json()["redirect_to"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_CB,
            "client_id": client_id, "code_verifier": verifier}
    tokens = api.post("/oauth/token", data=form).json()
    assert api.get("/auth/me", headers=_bearer(tokens)).status_code == 200
    assert api.post("/oauth/token", data=form).json()["error"] == "invalid_grant"
    # The first exchange's tokens are dead too: a replayed code means it leaked.
    assert api.get("/auth/me", headers=_bearer(tokens)).status_code == 401
    assert db.query(models.OAuthGrant).one().revoked_at is not None


def test_expired_code_is_refused(web, api, db):
    client_id = _register(api).json()["client_id"]
    verifier, challenge = _pkce()
    params = _authorize_params(client_id, challenge)
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "allow"})
    code = parse_qs(urlsplit(r.json()["redirect_to"]).query)["code"][0]
    row = db.query(models.OAuthAuthorizationCode).one()
    row.expires_at = oauth._utcnow() - timedelta(seconds=1)
    db.commit()
    t = api.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_CB,
                                       "client_id": client_id, "code_verifier": verifier})
    assert t.json()["error"] == "invalid_grant"


def test_unknown_grant_type_and_client(api):
    assert api.post("/oauth/token", data={"grant_type": "password"}).json()["error"] == "unsupported_grant_type"
    r = api.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": "nope", "code": "x"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_token_endpoint_accepts_json_too(web, api):
    client_id = _register(api).json()["client_id"]
    verifier, challenge = _pkce()
    params = _authorize_params(client_id, challenge)
    r = web.post("/oauth/authorize/decide", json={"params": params, "decision": "allow"})
    code = parse_qs(urlsplit(r.json()["redirect_to"]).query)["code"][0]
    t = api.post("/oauth/token", json={"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_CB,
                                       "client_id": client_id, "code_verifier": verifier})
    assert t.status_code == 200 and t.headers["cache-control"] == "no-store"


# ── refresh rotation ──────────────────────────────────────────

def test_refresh_rotates_and_the_old_pair_dies(web, api):
    tokens, client_id, _ = _consent(web, api)
    r = api.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                       "client_id": client_id})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"] != tokens["access_token"] and new["refresh_token"] != tokens["refresh_token"]
    assert api.get("/auth/me", headers=_bearer(new)).status_code == 200
    assert api.get("/auth/me", headers=_bearer(tokens)).status_code == 401


def test_reusing_a_rotated_refresh_token_revokes_everything(web, api, db):
    tokens, client_id, _ = _consent(web, api)
    new = api.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                         "client_id": client_id}).json()
    replay = api.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                            "client_id": client_id})
    assert replay.json()["error"] == "invalid_grant"
    assert api.get("/auth/me", headers=_bearer(new)).status_code == 401
    assert db.query(models.OAuthGrant).one().revoked_at is not None


def test_refresh_cannot_widen_scope_or_cross_clients(web, api):
    tokens, client_id, _ = _consent(web, api, scope="jobs:read")
    r = api.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                       "client_id": client_id, "scope": "jobs:read jobs:write"})
    assert r.json()["error"] == "invalid_scope"
    other = _register(api).json()["client_id"]
    r = api.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                       "client_id": other})
    assert r.json()["error"] == "invalid_grant"


# ── what a bearer may do ──────────────────────────────────────

def test_bearer_cannot_leave_the_jobs_surface(web, api):
    _job(api)
    tokens, _, _ = _consent(web, api)
    h = _bearer(tokens)
    rid = api.get("/jobs", headers=h).json()["items"][0]["id"]
    for method, path in [("GET", "/keys"), ("GET", "/profile"), ("GET", "/agent/keys"), ("GET", "/admin/users"),
                         ("DELETE", f"/jobs/{rid}"), ("POST", "/jobs/manual"),
                         ("POST", f"/jobs/{rid}/interview-prep"), ("GET", "/oauth/connected-apps"),
                         ("POST", "/auth/change-password")]:
        r = api.request(method, path, headers=h, json={})
        assert r.status_code == 403, (method, path, r.status_code)


def test_bearer_needs_the_scope_the_user_granted(web, api):
    _job(api)
    tokens, _, _ = _consent(web, api, scope="jobs:read")
    h = _bearer(tokens)
    rid = api.get("/jobs", headers=h).json()["items"][0]["id"]
    r = api.patch(f"/jobs/{rid}", headers=h, json={"starred": True})
    assert r.status_code == 403
    assert 'insufficient_scope' in r.headers["www-authenticate"] and "jobs:write" in r.headers["www-authenticate"]
    assert api.post(f"/jobs/{rid}/research", headers=h).status_code == 403


def test_generate_scope_starts_research(web, api, monkeypatch):
    from app.routers import generate as generate_mod
    from app.security import encrypt_api_key
    _job(api)
    tokens, _, _ = _consent(web, api)
    h = _bearer(tokens)
    rid = api.get("/jobs", headers=h).json()["items"][0]["id"]
    assert api.post(f"/jobs/{rid}/research", headers=h).status_code == 400     # no key yet: preflight
    # Give the user a key (through the browser, where keys are managed).
    from app.database import SessionLocal  # noqa: F401
    import app.models as m
    db = next(app.dependency_overrides[get_db]())
    db.add(m.UserAPIKey(user_id=TEST_USER_ID, provider=m.LLMProvider.ANTHROPIC,
                        encrypted_key=encrypt_api_key("sk-test"), preferred_model="claude-haiku-4-5"))
    db.add(m.Profile(user_id=TEST_USER_ID, name="default", is_active=True, resume_text="x"))
    db.commit()
    monkeypatch.setattr(generate_mod, "llm_complete", lambda *a, **k: "Acme builds widgets.")
    r = api.post(f"/jobs/{rid}/research", headers=h)
    assert r.status_code == 202 and r.json()["source"] == "anthropic"      # attributed to the app
    assert api.get(f"/jobs/{rid}", headers=h).json()["research_summary"] == "Acme builds widgets."
    events = {e["event_type"]: e["description"] for e in api.get(f"/jobs/{rid}/timeline", headers=h).json()}
    assert events["research_generated"] == "Company research generated via Claude (Anthropic)"


def test_expired_revoked_and_garbage_bearers_are_401(web, api, db):
    tokens, _, _ = _consent(web, api)
    tok = db.query(models.OAuthToken).filter_by(kind="access").one()
    tok.expires_at = oauth._utcnow() - timedelta(seconds=1)
    db.commit()
    r = api.get("/auth/me", headers=_bearer(tokens))
    assert r.status_code == 401 and 'invalid_token' in r.headers["www-authenticate"]
    assert api.get("/auth/me", headers={"Authorization": "Bearer jr_at_garbage"}).status_code == 401
    # A non-OAuth bearer isn't ours to judge: falls through to the cookie path, which has none.
    assert api.get("/auth/me", headers={"Authorization": "Bearer something-else"}).status_code == 401


# ── revocation and Connected apps ─────────────────────────────

def test_revocation_endpoint_kills_the_grant(web, api):
    tokens, client_id, _ = _consent(web, api)
    assert api.post("/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": client_id}).status_code == 200
    assert api.get("/auth/me", headers=_bearer(tokens)).status_code == 401
    # Unknown tokens are silently accepted, per RFC 7009.
    assert api.post("/oauth/revoke", data={"token": "nope"}).status_code == 200


def test_connected_apps_lists_and_disconnects(web, api):
    tokens, _, _ = _consent(web, api, scope="jobs:read generate", name="Claude Desktop")
    apps = web.get("/oauth/connected-apps").json()
    assert len(apps) == 1
    assert apps[0]["vendor_label"] == "Claude (Anthropic)" and apps[0]["client_name"] == "Claude Desktop"
    assert apps[0]["scopes"] == ["jobs:read", "generate"]
    assert web.delete(f"/oauth/connected-apps/{apps[0]['id']}").status_code == 204
    assert web.get("/oauth/connected-apps").json() == []
    assert api.get("/auth/me", headers=_bearer(tokens)).status_code == 401


def test_cannot_disconnect_someone_elses_app(web, api, db):
    import uuid
    _consent(web, api)
    grant = db.query(models.OAuthGrant).one()
    other = models.User(id=uuid.uuid4(), email="o@example.com", password_hash="x", is_approved=True)
    db.add(other)
    db.commit()
    grant.user_id = other.id
    db.commit()
    assert web.delete(f"/oauth/connected-apps/{grant.id}").status_code == 404
    assert web.get("/oauth/connected-apps").json() == []
