"""OAuth 2.1 authorization server for AI assistants connecting as the user.

Claude, ChatGPT, Gemini and Grok add Job Radar as an MCP connector and sign
the user in here. This module is the whole protocol surface behind
routers/oauth.py; it is deliberately a narrow profile rather than a general
server:

  * one grant: authorization_code with PKCE S256, plus refresh_token;
  * public clients only (token_endpoint_auth_method "none") — every vendor's
    hosted app registers that way, and a client secret would buy nothing;
  * clients admitted ONLY by their redirect URI matching VENDOR_REDIRECTS. A
    Dynamic Client Registration (RFC 7591) or a Client ID Metadata Document
    can call itself anything; the redirect host is the one thing an impostor
    cannot choose, because the code is delivered there. The consent page
    names the vendor derived from that host, and shows the self-asserted
    client_name second;
  * no loopback redirects. Developer tools (Claude Code, Gemini CLI, Codex)
    redirect to localhost, where any process can bind the port; they will get
    pasted tokens from Settings instead;
  * tokens are random, stored hashed, short-lived (access 1 h) with rotating
    refresh tokens (30 d). Presenting a rotated-out refresh token again is
    treated as theft and revokes the whole grant.

Why hand-written: the MCP Python SDK's server-side auth needs pydantic ≥ 2.11
(tracker-api is pinned to 2.10 behind the deferred LiteLLM upgrade) and has
neither CIMD on the server side nor a redirect allowlist — the two parts that
matter here. authlib has no Starlette server integration. So the protocol is
written out, kept small, and pinned by tests/test_oauth.py.

What a bearer token may call is in ROUTE_POLICY, enforced in
deps.get_current_user: the jobs surface only, DELETE never, and each route
needs the scope the user agreed to.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger(__name__)

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
CIMD_REFETCH_AFTER = timedelta(hours=24)
CIMD_MAX_BYTES = 64 * 1024
CIMD_TIMEOUT = 5.0
# A bearer touching the API updates the grant's last_used_at, but not on every
# call — once per this interval is enough for "last used 2 minutes ago".
LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)

ACCESS_PREFIX = "jr_at_"
REFRESH_PREFIX = "jr_rt_"


# ── Vendor allowlist ──────────────────────────────────────────
#
# Exact strings where the vendor publishes a fixed callback, a tight pattern
# where the callback carries a per-connection id. Sources: Claude's connector
# docs (exact); OpenAI's connector docs (stable URI, plus per-callback-id form);
# Gemini and Grok from multiple independent operator reports — verify against a
# live connector before relying on them. Any change here is a security change.

VENDORS = {
    "anthropic": "Claude (Anthropic)",
    "openai":    "ChatGPT (OpenAI)",
    "google":    "Gemini (Google)",
    "xai":       "Grok (xAI)",
}

_EXACT_REDIRECTS = {
    "https://claude.ai/api/mcp/auth_callback":                "anthropic",
    "https://chatgpt.com/connector_platform_oauth_redirect":  "openai",
    "https://grok.com/connectors-oauth-exchange-code/":       "xai",
}
_PATTERN_REDIRECTS = [
    (re.compile(r"^https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}$"), "openai"),
    (re.compile(r"^https://oauth-redirect\.googleusercontent\.com/r/user_bound_custom-mcp-[A-Za-z0-9._-]{1,200}$"), "google"),
]

# Hosts a Client ID Metadata Document may be fetched from. This is NOT the
# admission control — the redirect allowlist above is load-bearing, and open
# DCR admits the same clients anyway. Host matching does two lesser jobs:
# it keeps the fetch off anything but a vendor origin (so the server never
# requests an arbitrary or private address on a client's say-so), and it
# labels the vendor so a document can't claim to be Claude while redirecting
# to ChatGPT. Kept to specific hosts: a broad suffix such as
# googleusercontent.com would include user-upload hosts.
_CIMD_HOSTS = {
    "anthropic": ("claude.ai", "claude.com"),
    "openai":    ("chatgpt.com", "openai.com"),
    "google":    ("gemini.google.com", "accounts.google.com", "oauth-redirect.googleusercontent.com"),
    "xai":       ("grok.com", "x.ai"),
}


def vendor_for_redirect(uri: str) -> str | None:
    """The vendor a redirect URI belongs to, or None if it is not allowlisted."""
    if uri in _EXACT_REDIRECTS:
        return _EXACT_REDIRECTS[uri]
    for pattern, vendor in _PATTERN_REDIRECTS:
        if pattern.match(uri):
            return vendor
    return None


def vendor_for_redirects(uris: list[str]) -> str:
    """One vendor for the whole list, or raise. Mixed vendors in one client is
    not a configuration any real assistant produces."""
    if not uris:
        raise RegistrationError("invalid_redirect_uri", "redirect_uris is required.")
    vendors = set()
    for uri in uris:
        if not isinstance(uri, str) or len(uri) > 2048:
            raise RegistrationError("invalid_redirect_uri", "Malformed redirect_uri.")
        vendor = vendor_for_redirect(uri)
        if vendor is None:
            raise RegistrationError(
                "invalid_redirect_uri",
                f"redirect_uri is not one Job Radar accepts: {uri}. Job Radar connects to "
                "Claude, ChatGPT, Gemini and Grok only.",
            )
        vendors.add(vendor)
    if len(vendors) != 1:
        raise RegistrationError("invalid_redirect_uri", "redirect_uris must all belong to one assistant.")
    return vendors.pop()


def _cimd_vendor_for_host(host: str) -> str | None:
    host = host.lower()
    for vendor, suffixes in _CIMD_HOSTS.items():
        for s in suffixes:
            if host == s or host.endswith("." + s):
                return vendor
    return None


# ── Errors ────────────────────────────────────────────────────

class RegistrationError(Exception):
    """RFC 7591 §3.2.2 error. Rendered by the router as 400 JSON."""
    def __init__(self, error: str, description: str):
        super().__init__(description)
        self.error, self.description = error, description


class AuthorizeError(Exception):
    """A problem with an authorization request. `redirect_to` is set only once
    the redirect_uri itself has been verified — before that, the user must see
    the error on our page, never be sent to an unverified address."""
    def __init__(self, error: str, description: str, redirect_to: str | None = None):
        super().__init__(description)
        self.error, self.description, self.redirect_to = error, description, redirect_to


class TokenError(Exception):
    """RFC 6749 §5.2 error. 400, or 401 for invalid_client."""
    def __init__(self, error: str, description: str = ""):
        super().__init__(description)
        self.error, self.description = error, description


# ── Small helpers ─────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:        # SQLite in tests
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def is_bearer(authorization: str) -> bool:
    parts = authorization.split(None, 1)
    return len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].startswith(ACCESS_PREFIX)


def parse_scope(scope: str | None) -> list[str]:
    """Validated, de-duplicated, in canonical order. Empty = every scope: the
    vendors request what our metadata advertises, and a client that sends
    nothing is asking for the lot."""
    if not scope or not scope.strip():
        return list(models.OAUTH_SCOPES)
    asked = scope.split()
    unknown = [s for s in asked if s not in models.OAUTH_SCOPES]
    if unknown:
        raise ValueError(" ".join(unknown))
    return [s for s in models.OAUTH_SCOPES if s in asked]


def _add_query(url: str, params: dict) -> str:
    parts = urlsplit(url)
    query = parts.query + ("&" if parts.query else "") + urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


# ── Metadata (RFC 8414) ───────────────────────────────────────

def metadata(base_url: str) -> dict:
    base = base_url.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/api/oauth/token",
        "registration_endpoint": f"{base}/api/oauth/register",
        "revocation_endpoint": f"{base}/api/oauth/revoke",
        "scopes_supported": list(models.OAUTH_SCOPES),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        # Claude uses its hosted metadata document only when BOTH this and
        # "none" above are advertised; otherwise it falls back to registering.
        "client_id_metadata_document_supported": True,
        # ChatGPT uses its one stable callback when the issuer is echoed in the
        # authorization response (RFC 9207); otherwise a per-connection one.
        "authorization_response_iss_parameter_supported": True,
    }


# ── Clients: DCR and CIMD ─────────────────────────────────────

def register_client(db: Session, body: dict) -> models.OAuthClient:
    """RFC 7591. Admits a public client whose redirect URIs all belong to one
    supported vendor; refuses everything else with the RFC's error shape."""
    if not isinstance(body, dict):
        raise RegistrationError("invalid_client_metadata", "Body must be a JSON object.")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list):
        raise RegistrationError("invalid_redirect_uri", "redirect_uris must be a list.")
    vendor = vendor_for_redirects(uris)

    auth_method = body.get("token_endpoint_auth_method") or "none"
    if auth_method != "none":
        raise RegistrationError("invalid_client_metadata",
                                "Only public clients (token_endpoint_auth_method \"none\") are supported.")
    grants = body.get("grant_types") or ["authorization_code"]
    if not set(grants) <= {"authorization_code", "refresh_token"}:
        raise RegistrationError("invalid_client_metadata", "Only authorization_code and refresh_token grants are supported.")
    if not set(body.get("response_types") or ["code"]) <= {"code"}:
        raise RegistrationError("invalid_client_metadata", "Only the code response type is supported.")

    name = body.get("client_name")
    name = name[:100] if isinstance(name, str) and name.strip() else None
    client = models.OAuthClient(
        id="jr_client_" + secrets.token_urlsafe(16), kind=models.OAUTH_CLIENT_DCR,
        vendor=vendor, client_name=name, redirect_uris=list(dict.fromkeys(uris)),
    )
    db.add(client)
    db.commit()
    logger.info("oauth: registered %s client %s (%s)", vendor, client.id, name)
    return client


def _fetch_cimd(url: str) -> dict:
    """The network part of CIMD, split out so tests can replace it. Only
    reached after the host passed the vendor check."""
    with httpx.Client(timeout=CIMD_TIMEOUT, follow_redirects=False) as http:
        r = http.get(url, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise AuthorizeError("invalid_client", f"Client metadata document returned HTTP {r.status_code}.")
    if len(r.content) > CIMD_MAX_BYTES:
        raise AuthorizeError("invalid_client", "Client metadata document is too large.")
    try:
        return json.loads(r.content)
    except ValueError:
        raise AuthorizeError("invalid_client", "Client metadata document is not JSON.")


def _load_cimd_client(db: Session, url: str) -> models.OAuthClient:
    """A client identified by a Client ID Metadata Document: client_id is an
    HTTPS URL on a vendor host; the document there must say the same client_id
    and list redirect_uris that pass the same allowlist as DCR."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.fragment or len(url) > 512:
        raise AuthorizeError("invalid_client", "Unknown client.")
    host_vendor = _cimd_vendor_for_host(parts.hostname or "")
    if host_vendor is None:
        raise AuthorizeError("invalid_client",
                             "Job Radar connects to Claude, ChatGPT, Gemini and Grok only.")

    client = db.get(models.OAuthClient, url)
    fetched = _aware(client.metadata_fetched_at) if client else None
    if client is not None and fetched is not None and _utcnow() - fetched < CIMD_REFETCH_AFTER:
        return client

    doc = _fetch_cimd(url)
    if not isinstance(doc, dict) or doc.get("client_id") != url:
        raise AuthorizeError("invalid_client", "Client metadata document does not name itself as client_id.")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list):
        raise AuthorizeError("invalid_client", "Client metadata document lists no redirect_uris.")
    try:
        vendor = vendor_for_redirects(uris)
    except RegistrationError as e:
        raise AuthorizeError("invalid_client", e.description)
    if vendor != host_vendor:
        raise AuthorizeError("invalid_client", "Client metadata document's redirect_uris belong to a different assistant.")
    if (doc.get("token_endpoint_auth_method") or "none") != "none":
        raise AuthorizeError("invalid_client", "Only public clients are supported.")
    name = doc.get("client_name")
    name = name[:100] if isinstance(name, str) and name.strip() else None

    if client is None:
        client = models.OAuthClient(id=url, kind=models.OAUTH_CLIENT_CIMD, vendor=vendor)
        db.add(client)
    client.vendor, client.client_name = vendor, name
    client.redirect_uris = list(dict.fromkeys(uris))
    client.metadata_fetched_at = _utcnow()
    db.commit()
    return client


def load_client(db: Session, client_id: str) -> models.OAuthClient:
    if not client_id:
        raise AuthorizeError("invalid_client", "Unknown client.")
    if client_id.startswith("https://"):
        return _load_cimd_client(db, client_id)
    client = db.get(models.OAuthClient, client_id)
    if client is None:
        raise AuthorizeError("invalid_client", "Unknown client.")
    return client


# ── Authorization request → code ──────────────────────────────

_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def check_authorization_request(db: Session, params: dict) -> tuple[models.OAuthClient, list[str]]:
    """Validate the request the consent page received. Returns (client, scopes).
    Raises AuthorizeError — without redirect_to until the redirect_uri is known
    to be the client's own, with it afterwards."""
    client = load_client(db, params.get("client_id") or "")
    redirect_uri = params.get("redirect_uri") or ""
    if redirect_uri not in (client.redirect_uris or []):
        raise AuthorizeError("invalid_request", "redirect_uri is not registered for this client.")

    state = params.get("state")
    base = {"state": state} if state else {}

    def back(error: str, description: str) -> AuthorizeError:
        return AuthorizeError(error, description,
                              _add_query(redirect_uri, {"error": error, "error_description": description, **base}))

    if (params.get("response_type") or "") != "code":
        raise back("unsupported_response_type", "Only response_type=code is supported.")
    if (params.get("code_challenge_method") or "") != "S256":
        raise back("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    if not _CHALLENGE_RE.match(params.get("code_challenge") or ""):
        raise back("invalid_request", "code_challenge is missing or malformed.")
    try:
        scopes = parse_scope(params.get("scope"))
    except ValueError as e:
        raise back("invalid_scope", f"Unknown scope: {e}")
    # RFC 8707 resource indicator: validated and recorded on the code, but
    # tokens are NOT audience-bound to it. Deliberate: there is one resource
    # server (the MCP connector, which forwards the bearer here), so binding
    # would buy nothing yet. If a second resource server ever appears, bind
    # tokens to `resource` and check it in user_from_bearer.
    resource = params.get("resource")
    if resource and (len(resource) > 512 or not resource.startswith("https://")):
        raise back("invalid_target", "resource must be an https URL.")
    return client, scopes


def issue_code(db: Session, user: models.User, client: models.OAuthClient, params: dict,
               scopes: list[str], base_url: str) -> str:
    """The user said yes. Returns the URL to send them back to."""
    raw = secrets.token_urlsafe(32)
    db.add(models.OAuthAuthorizationCode(
        code_hash=_hash(raw), client_id=client.id, user_id=user.id,
        redirect_uri=params["redirect_uri"], scope=" ".join(scopes),
        code_challenge=params["code_challenge"], resource=params.get("resource") or None,
        expires_at=_utcnow() + CODE_TTL,
    ))
    db.commit()
    out = {"code": raw, "iss": base_url.rstrip("/")}
    if params.get("state"):
        out["state"] = params["state"]
    return _add_query(params["redirect_uri"], out)


def deny_url(params: dict, base_url: str) -> str:
    out = {"error": "access_denied", "error_description": "The user declined.", "iss": base_url.rstrip("/")}
    if params.get("state"):
        out["state"] = params["state"]
    return _add_query(params["redirect_uri"], out)


# ── Token endpoint ────────────────────────────────────────────

def _issue_tokens(db: Session, grant: models.OAuthGrant) -> tuple[dict, models.OAuthToken, models.OAuthToken]:
    access, refresh = _new_token(ACCESS_PREFIX), _new_token(REFRESH_PREFIX)
    now = _utcnow()
    access_row = models.OAuthToken(grant_id=grant.id, kind=models.OAUTH_TOKEN_ACCESS,
                                   token_hash=_hash(access), expires_at=now + ACCESS_TTL)
    refresh_row = models.OAuthToken(grant_id=grant.id, kind=models.OAUTH_TOKEN_REFRESH,
                                    token_hash=_hash(refresh), expires_at=now + REFRESH_TTL)
    db.add_all([access_row, refresh_row])
    db.commit()
    return {
        "access_token": access, "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh, "scope": grant.scope,
    }, access_row, refresh_row


def _verify_pkce(verifier: str, challenge: str) -> bool:
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, challenge)


def exchange_code(db: Session, form: dict) -> dict:
    """grant_type=authorization_code. Every check fails the same way
    (invalid_grant) so nothing leaks about which part was wrong."""
    client_id = form.get("client_id") or ""
    client = db.get(models.OAuthClient, client_id) if client_id else None
    if client is None:
        raise TokenError("invalid_client", "Unknown client.")
    row = db.get(models.OAuthAuthorizationCode, _hash(form.get("code") or ""))
    now = _utcnow()
    if (row is None or row.client_id != client.id or row.used_at is not None
            or _aware(row.expires_at) < now
            or row.redirect_uri != (form.get("redirect_uri") or "")
            or not _verify_pkce(form.get("code_verifier") or "", row.code_challenge)):
        if row is not None and row.used_at is not None:
            # A code presented twice is the textbook sign it was stolen in
            # transit: kill whatever the first use produced.
            _revoke_grants_for(db, row.user_id, row.client_id)
        raise TokenError("invalid_grant", "The authorization code is invalid, expired or already used.")
    row.used_at = now
    grant = models.OAuthGrant(user_id=row.user_id, client_id=client.id, scope=row.scope)
    db.add(grant)
    db.commit()
    logger.info("oauth: new grant %s for user %s via %s", grant.id, row.user_id, client.vendor)
    return _issue_tokens(db, grant)[0]


def refresh(db: Session, form: dict) -> dict:
    """grant_type=refresh_token, rotating. Presenting a refresh token that has
    already been rotated out is treated as theft: the whole grant is revoked."""
    client_id = form.get("client_id") or ""
    client = db.get(models.OAuthClient, client_id) if client_id else None
    if client is None:
        raise TokenError("invalid_client", "Unknown client.")
    tok = (db.query(models.OAuthToken)
           .filter(models.OAuthToken.token_hash == _hash(form.get("refresh_token") or ""),
                   models.OAuthToken.kind == models.OAUTH_TOKEN_REFRESH)
           .first())
    if tok is None or tok.grant.client_id != client.id:
        raise TokenError("invalid_grant", "The refresh token is invalid.")
    grant = tok.grant
    now = _utcnow()
    if tok.replaced_by is not None or tok.revoked_at is not None:
        logger.warning("oauth: rotated-out refresh token presented for grant %s — revoking", grant.id)
        revoke_grant(db, grant)
        raise TokenError("invalid_grant", "The refresh token has been used before.")
    if grant.revoked_at is not None or _aware(tok.expires_at) < now:
        raise TokenError("invalid_grant", "The refresh token has expired or been revoked.")
    asked = form.get("scope")
    if asked and not set(asked.split()) <= set(grant.scope.split()):
        raise TokenError("invalid_scope", "Cannot widen the scope on refresh.")
    tokens, new_access, new_refresh = _issue_tokens(db, grant)
    tok.replaced_by = new_refresh.id
    tok.revoked_at = now
    # Every earlier access token dies with the old refresh token — by id, not by
    # timestamp, so the pair just issued can never be caught by its own rotation.
    (db.query(models.OAuthToken)
       .filter(models.OAuthToken.grant_id == grant.id,
               models.OAuthToken.kind == models.OAUTH_TOKEN_ACCESS,
               models.OAuthToken.id != new_access.id,
               models.OAuthToken.revoked_at.is_(None))
       .update({models.OAuthToken.revoked_at: now}, synchronize_session=False))
    db.commit()
    return tokens


def revoke_grant(db: Session, grant: models.OAuthGrant) -> None:
    now = _utcnow()
    if grant.revoked_at is None:
        grant.revoked_at = now
    (db.query(models.OAuthToken)
       .filter(models.OAuthToken.grant_id == grant.id, models.OAuthToken.revoked_at.is_(None))
       .update({models.OAuthToken.revoked_at: now}, synchronize_session=False))
    db.commit()


def _revoke_grants_for(db: Session, user_id, client_id: str) -> None:
    for g in (db.query(models.OAuthGrant)
                .filter(models.OAuthGrant.user_id == user_id, models.OAuthGrant.client_id == client_id,
                        models.OAuthGrant.revoked_at.is_(None)).all()):
        revoke_grant(db, g)


def revoke_token(db: Session, form: dict) -> None:
    """RFC 7009. Finds the token by hash whatever its kind and revokes its
    grant. Silent on unknown tokens, as the RFC requires."""
    raw = form.get("token") or ""
    if not raw:
        return
    tok = db.query(models.OAuthToken).filter(models.OAuthToken.token_hash == _hash(raw)).first()
    if tok is None:
        return
    client_id = form.get("client_id")
    if client_id and tok.grant.client_id != client_id:
        return
    revoke_grant(db, tok.grant)


# ── Bearer tokens on the API ──────────────────────────────────
#
# What a connected app may call, and the scope each route needs. Anything not
# listed is refused for a bearer token whatever its scopes — the assistant
# gets the jobs surface and nothing else (no keys, no profile, no admin, no
# agent endpoints, and never DELETE).

ROUTE_POLICY: list[tuple[str, re.Pattern, str]] = [
    ("GET",   re.compile(r"^/auth/me$"),                                            models.OAUTH_SCOPE_JOBS_READ),
    ("GET",   re.compile(r"^/jobs$"),                                               models.OAUTH_SCOPE_JOBS_READ),
    ("GET",   re.compile(r"^/jobs/[0-9a-fA-F-]{36}$"),                              models.OAUTH_SCOPE_JOBS_READ),
    ("GET",   re.compile(r"^/jobs/[0-9a-fA-F-]{36}/(timeline|tailor-resume|generation(/[0-9a-fA-F-]{36})?)$"),
                                                                                    models.OAUTH_SCOPE_JOBS_READ),
    ("PATCH", re.compile(r"^/jobs/[0-9a-fA-F-]{36}$"),                              models.OAUTH_SCOPE_JOBS_WRITE),
    ("POST",  re.compile(r"^/jobs/[0-9a-fA-F-]{36}/notes$"),                        models.OAUTH_SCOPE_JOBS_WRITE),
    ("POST",  re.compile(r"^/jobs/[0-9a-fA-F-]{36}/(research|tailor-resume)$"),     models.OAUTH_SCOPE_GENERATE),
]


def route_scope(method: str, path: str) -> str | None:
    for m, pattern, scope in ROUTE_POLICY:
        if m == method and pattern.match(path):
            return scope
    return None


def user_from_bearer(authorization: str, request, db: Session) -> models.User:
    """Resolve a connected app's token to its user, hold it to ROUTE_POLICY, and
    leave the vendor on `request.state.oauth_vendor` so what the app does can be
    attributed to it (generation jobs, timeline)."""
    method, path = request.method, request.url.path
    raw = authorization.split(None, 1)[1]
    tok = (db.query(models.OAuthToken)
           .filter(models.OAuthToken.token_hash == _hash(raw), models.OAuthToken.kind == models.OAUTH_TOKEN_ACCESS)
           .first())
    now = _utcnow()
    if (tok is None or tok.revoked_at is not None or _aware(tok.expires_at) < now
            or tok.grant.revoked_at is not None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token",
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    grant = tok.grant
    needed = route_scope(method, path)
    if needed is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Connected apps cannot use this part of Job Radar.")
    if needed not in grant.scope.split():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"This needs the {needed} permission, which you didn't grant.",
                            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{needed}"'})
    user = grant.user
    if user is None or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token",
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    last = _aware(grant.last_used_at)
    if last is None or now - last > LAST_USED_WRITE_INTERVAL:
        grant.last_used_at = now
        db.commit()
    request.state.oauth_vendor = grant.client.vendor
    request.state.oauth_grant_id = grant.id
    return user


def actor(request) -> str:
    """Who is acting: the vendor key of a connected app, else "web"."""
    return getattr(request.state, "oauth_vendor", None) or models.GENERATION_SOURCE_WEB


def sweep(db: Session, older_than: timedelta = timedelta(days=1)) -> dict[str, int]:
    """Nightly. Drop DCR registrations that never led to a grant: every fresh
    Claude connection registers a new client, and so can anyone with the URL,
    so without this the table only grows. A client with any grant, revoked or
    not, is kept — it is history on the Connected apps page. Also drop
    authorization codes past their expiry: used or not, a code is worthless a
    day later, and otherwise every successful connect leaves a row forever."""
    cutoff = _utcnow() - older_than
    codes = (db.query(models.OAuthAuthorizationCode)
               .filter(models.OAuthAuthorizationCode.expires_at < cutoff)
               .delete(synchronize_session=False))
    granted = db.query(models.OAuthGrant.client_id)
    clients = (db.query(models.OAuthClient)
                 .filter(models.OAuthClient.kind == models.OAUTH_CLIENT_DCR,
                         models.OAuthClient.created_at < cutoff,
                         models.OAuthClient.id.not_in(granted))
                 .delete(synchronize_session=False))
    db.commit()
    return {"oauth_clients_deleted": clients, "oauth_codes_deleted": codes}
