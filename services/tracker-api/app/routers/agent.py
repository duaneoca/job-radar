"""
Agent router — /agent/* endpoints.

Three auth classes:
  • Agent-facing   (X-Agent-Key header) — user derived from key (H1)
  • Frontend-facing (JWT cookie)        — existing get_current_user
  • Slack-facing   (signing-secret)     — HITL callback (C4)

Route ordering: all literal paths registered before /{id} param routes.
"""

import imaplib
import ipaddress
import json
import logging
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app import feature_flags, models, schemas
from app.config import settings
from app.database import get_db
from app.llm import get_active_llm_key
from app.deps import (
    get_agent_writer,
    get_current_admin,
    get_current_user,
    get_user_from_agent_key,
    require_internal_token,
)
from app.routers.jobs import apply_status_change
from app.security import (
    create_oauth_state,
    decode_oauth_state,
    decrypt_api_key,
    encrypt_api_key,
    generate_agent_key,
    verify_slack_signature,
)

# Google OAuth endpoints (Gmail cloud mailbox connect — JR-5)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_LABELS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/labels"

# Slack OAuth v2 endpoints (per-user "Add to Slack" notifications — JR-6)
SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_CONV_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_REVOKE_URL = "https://slack.com/api/auth.revoke"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# Agent-writable status subset (C1)
_AGENT_WRITABLE_STATUSES = {
    models.JobStatus.APPLIED,
    models.JobStatus.INTERVIEWING,
    models.JobStatus.OFFER,
    models.JobStatus.REJECTED,
}

_ALLOWED_LINK_SCHEMES = {"http", "https"}


def _validate_link(link: Optional[str]) -> Optional[str]:
    """Enforce http/https scheme allowlist (C2). Raises 422 on violation."""
    if link is None:
        return None
    scheme = urlparse(link).scheme.lower()
    if scheme not in _ALLOWED_LINK_SCHEMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Link must use http or https scheme, got: {scheme!r}",
        )
    return link


def _get_inbox_email_or_404(
    inbox_id: UUID, user: models.User, db: Session
) -> models.InboxEmail:
    row = (
        db.query(models.InboxEmail)
        .options(
            joinedload(models.InboxEmail.postings),
            joinedload(models.InboxEmail.interactions),
        )
        .filter(models.InboxEmail.id == inbox_id, models.InboxEmail.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Inbox entry not found")
    return row


def _get_review_owned(review_id: UUID, user: models.User, db: Session) -> models.UserJobReview:
    """Ownership-validated review fetch (H1)."""
    review = (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job), joinedload(models.UserJobReview.timeline))
        .filter(
            models.UserJobReview.id == review_id,
            models.UserJobReview.user_id == user.id,
        )
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


# ── Agent-facing endpoints (X-Agent-Key) ─────────────────────

def _build_agent_config(user: models.User, db: Session) -> schemas.AgentConfigOut:
    """Assemble a user's decrypted agent config (LLM key + email creds + folders).
    Shared by /agent/config (X-Agent-Key) and /agent/cloud/config/{user_id}
    (internal token). Returns DECRYPTED secrets — callers must be in-cluster only."""
    # LLM key — the user's *active* key (selection → priority), same as scoring/research.
    llm_config = None
    key_row = get_active_llm_key(user.id, db)
    if key_row:
        try:
            plaintext_key = decrypt_api_key(key_row.encrypted_key)
            llm_config = schemas.AgentLLMConfig(
                provider=key_row.provider.value,
                preferred_model=key_row.preferred_model,
                api_key=plaintext_key,
            )
        except Exception:
            logger.error("AUDIT /agent/config key decrypt failed user=%s", user.id)

    # Email credentials
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    email_provider = None
    folders = schemas.AgentFolderConfig(
        root=None, interaction=None, postings=None, social=None, unprocessed=None
    )
    email_credentials_blob = None

    if cred:
        if not settings.encryption_key:
            raise HTTPException(
                status_code=503,
                detail="ENCRYPTION_KEY not configured — cannot decrypt email credentials",
            )
        stored = None
        try:
            stored = json.loads(decrypt_api_key(cred.encrypted_blob))
        except Exception:
            logger.error("AUDIT /agent/config email cred decrypt failed user=%s", user.id)
        email_provider = cred.provider.value
        if stored is not None:
            if cred.provider == models.EmailProvider.GMAIL:
                # Stored blob holds only the per-user refresh_token (+scopes);
                # inject the shared Web-app client creds so the agent can build a
                # Google "authorized user" credential (from_authorized_user_info).
                email_credentials_blob = {
                    "provider": "gmail",
                    "refresh_token": stored.get("refresh_token"),
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "token_uri": GOOGLE_TOKEN_URL,
                    "scopes": stored.get("scopes") or settings.gmail_oauth_scopes.split(),
                }
            else:
                email_credentials_blob = stored
        folders = schemas.AgentFolderConfig(
            root=cred.folder_root,
            interaction=cred.folder_interaction,
            postings=cred.folder_postings,
            social=cred.folder_social,
            unprocessed=cred.folder_unprocessed,
        )

    # Per-user Slack notifier — only when connected AND a channel is chosen (JR-6).
    slack_config = None
    sc = (
        db.query(models.SlackConnection)
        .filter(models.SlackConnection.user_id == user.id)
        .first()
    )
    if sc and sc.channel_id:
        try:
            slack_config = schemas.AgentSlackConfig(
                bot_token=decrypt_api_key(sc.encrypted_bot_token),
                channel_id=sc.channel_id,
            )
        except Exception:
            logger.error("AUDIT /agent/config slack token decrypt failed user=%s", user.id)

    return schemas.AgentConfigOut(
        provider=email_provider,
        folders=folders,
        llm=llm_config,
        email_credentials=email_credentials_blob,
        enabled=bool(cred and cred.enabled),
        slack=slack_config,
    )


@router.get("/config", response_model=schemas.AgentConfigOut)
def get_agent_config(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_user_from_agent_key),
):
    """Decrypted LLM key + email credentials for the single-user agent (H6/H6a).
    MUST be called in-cluster only — nginx 404s it and the NetworkPolicy blocks
    external access. Every call is audit-logged as defense-in-depth."""
    logger.warning(
        "AUDIT /agent/config user=%s ip=%s",
        user.id,
        request.client.host if request.client else "unknown",
    )
    if not feature_flags.email_agent_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email agent disabled")
    return _build_agent_config(user, db)


# ── Cloud enumeration (internal-token, in-cluster only — JR-5 §2.1b) ──

@router.get("/cloud/users", response_model=list[schemas.CloudUserOut])
def cloud_list_users(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_internal_token),
):
    """Enabled cloud users that have stored mailbox credentials. NO secrets — the
    runner uses this to discover users, then fetches one config at a time (H6)."""
    logger.warning(
        "AUDIT /agent/cloud/users ip=%s",
        request.client.host if request.client else "unknown",
    )
    # Feature toggled off → enumerate nobody. The cloud CronJob keeps firing on
    # its k8s schedule but discovers zero users, making the whole run a no-op —
    # this is the app-level lever since the app can't touch k8s.
    if not feature_flags.email_agent_enabled(db):
        return []
    rows = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.enabled == True)  # noqa: E712
        .all()
    )
    return [
        schemas.CloudUserOut(user_id=c.user_id, provider=c.provider.value, enabled=c.enabled)
        for c in rows
    ]


@router.get("/cloud/config/{user_id}", response_model=schemas.AgentConfigOut)
def cloud_get_config(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_internal_token),
):
    """One user's decrypted config (same shape as /agent/config). The runner fetches
    this per user, processes, then discards — blast radius of one user (H6)."""
    logger.warning(
        "AUDIT /agent/cloud/config user=%s ip=%s",
        user_id,
        request.client.host if request.client else "unknown",
    )
    if not feature_flags.email_agent_enabled(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email agent disabled")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _build_agent_config(user, db)


# ── Gmail OAuth connect (cloud mailbox users — JR-5) ──────────────

@router.get("/oauth/start")
def gmail_oauth_start(user: models.User = Depends(get_current_user)):
    """Return the Google consent URL. The frontend redirects the browser to it.

    State is a short-lived signed token binding the redirect to this user (CSRF +
    user binding); the callback trusts it instead of a JWT cookie.
    """
    if not (settings.google_oauth_client_id and settings.google_oauth_redirect_uri):
        raise HTTPException(status_code=503, detail="Gmail OAuth is not configured on this server")
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.gmail_oauth_scopes,
        "access_type": "offline",   # required to receive a refresh_token
        "prompt": "consent",        # force a refresh_token even on re-consent
        "include_granted_scopes": "true",
        "state": create_oauth_state(str(user.id)),
    }
    return {"authorization_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/oauth/callback")
def gmail_oauth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Google redirects the browser here after consent. Exchange the code for a
    refresh_token, store it (encrypted), then bounce back to the Settings UI.

    No JWT cookie is guaranteed on this top-level navigation, so auth is the
    signed `state`. We only ever store the refresh_token + granted scopes; the
    shared client_id/secret are injected at /agent/config read time.
    """
    def _back(result: str) -> RedirectResponse:
        return RedirectResponse(url=f"/settings?gmail={result}", status_code=303)

    if error or not code or not state:
        return _back("error")
    user_id = decode_oauth_state(state)
    if not user_id:
        return _back("error")
    try:
        user_uuid = UUID(user_id)
    except (ValueError, TypeError):
        return _back("error")
    user = db.query(models.User).filter(models.User.id == user_uuid).first()
    if not user or not user.is_approved:
        return _back("error")
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret
            and settings.google_oauth_redirect_uri):
        return _back("error")

    try:
        resp = httpx.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=15)
        resp.raise_for_status()
        token = resp.json()
    except Exception:
        logger.error("Gmail OAuth token exchange failed user=%s", user_id)
        return _back("error")

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        # prompt=consent should always return one; if Google didn't, the user
        # must revoke prior access at myaccount.google.com and retry.
        logger.warning("Gmail OAuth returned no refresh_token user=%s", user_id)
        return _back("norefresh")

    scopes = (token.get("scope") or settings.gmail_oauth_scopes).split()
    blob = encrypt_api_key(json.dumps({"refresh_token": refresh_token, "scopes": scopes}))

    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user_uuid)
        .first()
    )
    if cred:
        cred.provider = models.EmailProvider.GMAIL
        cred.encrypted_blob = blob
    else:
        db.add(models.EmailCredential(
            user_id=user_uuid,
            provider=models.EmailProvider.GMAIL,
            encrypted_blob=blob,
            enabled=False,   # can't run until labels are set + verified, then enabled
        ))
    db.commit()
    return _back("connected")


# ── Email-credential status / folders / disconnect (cloud — JR-5) ──

def _credential_status(cred: Optional[models.EmailCredential]) -> schemas.EmailCredentialStatusOut:
    empty_folders = schemas.AgentFolderConfig(
        root=None, interaction=None, postings=None, social=None, unprocessed=None
    )
    if not cred:
        return schemas.EmailCredentialStatusOut(
            provider=None, connected=False, enabled=False,
            folders=empty_folders, updated_at=None,
        )
    blob = {}
    try:
        blob = json.loads(decrypt_api_key(cred.encrypted_blob))
    except Exception:
        blob = {}
    # Gmail = refresh_token present; IMAP = host present.
    connected = bool(blob.get("refresh_token") or blob.get("host"))
    return schemas.EmailCredentialStatusOut(
        provider=cred.provider.value,
        connected=connected,
        enabled=cred.enabled,
        folders=schemas.AgentFolderConfig(
            root=cred.folder_root,
            interaction=cred.folder_interaction,
            postings=cred.folder_postings,
            social=cred.folder_social,
            unprocessed=cred.folder_unprocessed,
        ),
        updated_at=cred.updated_at,
        imap_host=blob.get("host"),
        imap_username=blob.get("username"),
    )


@router.get("/email-credentials", response_model=schemas.EmailCredentialStatusOut)
def get_email_credentials(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Masked mailbox connection status for the Settings UI. Never returns secrets."""
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    return _credential_status(cred)


@router.put("/email-credentials", response_model=schemas.EmailCredentialStatusOut)
def update_email_credentials(
    payload: schemas.EmailCredentialUpdateIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Set folder/label config and the enable flag. Enabling requires all five
    folders/labels to be set AND to exist on the mailbox (verified per provider) —
    a partial layout breaks the agent at runtime. You can save a partial config while
    disabled. Requires a connected mailbox (created by the connect flow)."""
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="No mailbox connected")

    f = payload.folders
    _LABELS = {
        "root": "Root", "interaction": "Interaction", "postings": "Postings",
        "social": "Social", "unprocessed": "Unprocessed",
    }
    vals = {k: ((getattr(f, k) or "").strip() or None) for k in _LABELS}

    if payload.enabled:
        noun = "labels" if cred.provider == models.EmailProvider.GMAIL else "folders"
        missing = [_LABELS[k] for k, v in vals.items() if not v]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"All {noun} are required to enable the agent. Missing: " + ", ".join(missing),
            )
        names = list(vals.values())
        if cred.provider == models.EmailProvider.GMAIL:
            _verify_gmail_labels(cred, names)
        elif cred.provider == models.EmailProvider.IMAP:
            _verify_imap_stored(cred, names)

    cred.folder_root = vals["root"]
    cred.folder_interaction = vals["interaction"]
    cred.folder_postings = vals["postings"]
    cred.folder_social = vals["social"]
    cred.folder_unprocessed = vals["unprocessed"]
    cred.enabled = payload.enabled
    db.commit()
    db.refresh(cred)
    return _credential_status(cred)


def _assert_public_host(host: str) -> None:
    """Block IMAP verification against private/loopback addresses (SSRF guard) —
    an authenticated user could otherwise probe in-cluster services via the host field."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Couldn't resolve '{host}'. Check the IMAP server address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(status_code=400, detail="IMAP host must be a public mail server.")


def _parse_imap_list_line(line) -> tuple[Optional[str], str]:
    """Parse an IMAP LIST line `(flags) "<delim>" <name>` → (delimiter, full name).
    delimiter is the server's hierarchy separator (e.g. "/" or "."), or None for NIL."""
    decoded = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
    m = re.match(r'^\(([^)]*)\)\s+(NIL|"[^"]*"|\S+)\s+(.*)$', decoded)
    if not m:
        return None, ""
    delim_raw = m.group(2)
    delim = None if delim_raw == "NIL" else delim_raw.strip('"')
    name = m.group(3).strip()
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1]
    return delim, name


def _imap_folder_name(line: bytes) -> str:
    """Extract just the mailbox name from an IMAP LIST line."""
    return _parse_imap_list_line(line)[1]


def _verify_imap(host, port, username, password, use_ssl, folder_names):
    """Live-verify an IMAP mailbox before storing: reachability, login, and that
    each configured folder exists. Raises HTTPException(400) with a useful message."""
    _assert_public_host(host)
    try:
        conn = (imaplib.IMAP4_SSL(host, port, timeout=10) if use_ssl
                else imaplib.IMAP4(host, port, timeout=10))
    except (socket.timeout, TimeoutError):
        raise HTTPException(status_code=400, detail=f"Timed out connecting to {host}:{port}. Check the host, port, and SSL setting.")
    except (ConnectionRefusedError, OSError):
        raise HTTPException(status_code=400, detail=f"Couldn't connect to {host}:{port}. Check the host, port, and SSL setting.")

    try:
        try:
            conn.login(username, password)
        except imaplib.IMAP4.error:
            raise HTTPException(status_code=400, detail="Login failed — check the username and password.")

        if folder_names:
            typ, data = conn.list()
            if typ != "OK":
                raise HTTPException(status_code=400, detail="Connected, but couldn't list folders on the server.")
            existing = {_imap_folder_name(line) for line in data if line}
            missing = [f for f in folder_names if f not in existing]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="These folders don't exist on the server (create them first): " + ", ".join(missing),
                )
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _verify_imap_stored(cred: models.EmailCredential, folder_names) -> None:
    """Verify configured folders against a user's already-stored IMAP creds."""
    try:
        blob = json.loads(decrypt_api_key(cred.encrypted_blob))
    except Exception:
        raise HTTPException(status_code=400, detail="IMAP connection is invalid — reconnect it.")
    _verify_imap(
        (blob.get("host") or "").strip(), int(blob.get("port") or 993),
        (blob.get("username") or "").strip(), blob.get("password") or "",
        bool(blob.get("use_ssl", True)), folder_names,
    )


def _verify_gmail_labels(cred: models.EmailCredential, label_names) -> None:
    """Confirm each configured Gmail label exists (the agent never creates labels).
    Refreshes an access token from the stored refresh_token. Raises HTTPException(400)."""
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        return  # no app creds configured (local/dev) — can't verify; don't block
    try:
        refresh_token = json.loads(decrypt_api_key(cred.encrypted_blob)).get("refresh_token")
    except Exception:
        refresh_token = None
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Gmail connection is invalid — reconnect it.")
    try:
        tok = httpx.post(GOOGLE_TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
        }, timeout=15)
        tok.raise_for_status()
        access = tok.json().get("access_token")
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't reach Gmail to verify labels. Try again in a moment.")
    if not access:
        raise HTTPException(status_code=400, detail="Gmail authorization expired — reconnect it.")
    try:
        resp = httpx.get(GMAIL_LABELS_URL, headers={"Authorization": f"Bearer {access}"}, timeout=15)
        resp.raise_for_status()
        existing = {lab.get("name") for lab in resp.json().get("labels", [])}
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't list your Gmail labels to verify. Try again.")
    missing = [n for n in label_names if n not in existing]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="These Gmail labels don't exist (create them first): " + ", ".join(missing),
        )


def _imap_list_folders(cred: models.EmailCredential) -> tuple[Optional[str], list[str]]:
    """List the mailbox's folders (full hierarchical names) + the server's delimiter,
    using the user's already-stored IMAP creds. Powers the folder picker."""
    try:
        blob = json.loads(decrypt_api_key(cred.encrypted_blob))
    except Exception:
        raise HTTPException(status_code=400, detail="IMAP connection is invalid — reconnect it.")
    host = (blob.get("host") or "").strip()
    port = int(blob.get("port") or 993)
    use_ssl = bool(blob.get("use_ssl", True))
    _assert_public_host(host)
    try:
        conn = (imaplib.IMAP4_SSL(host, port, timeout=10) if use_ssl
                else imaplib.IMAP4(host, port, timeout=10))
    except (socket.timeout, TimeoutError):
        raise HTTPException(status_code=400, detail=f"Timed out connecting to {host}:{port}.")
    except (ConnectionRefusedError, OSError):
        raise HTTPException(status_code=400, detail=f"Couldn't connect to {host}:{port}.")
    try:
        try:
            conn.login((blob.get("username") or "").strip(), blob.get("password") or "")
        except imaplib.IMAP4.error:
            raise HTTPException(status_code=400, detail="Login failed — reconnect the mailbox.")
        typ, data = conn.list()
        if typ != "OK":
            raise HTTPException(status_code=400, detail="Connected, but couldn't list folders on the server.")
        delimiter = None
        names: list[str] = []
        for line in data:
            if not line:
                continue
            d, name = _parse_imap_list_line(line)
            if delimiter is None and d:
                delimiter = d
            if name:
                names.append(name)
        return delimiter, sorted(set(names))
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _gmail_list_labels(cred: models.EmailCredential) -> list[str]:
    """List the user's Gmail labels (user-created only) for the picker."""
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        return []
    try:
        refresh_token = json.loads(decrypt_api_key(cred.encrypted_blob)).get("refresh_token")
    except Exception:
        refresh_token = None
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Gmail connection is invalid — reconnect it.")
    try:
        tok = httpx.post(GOOGLE_TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
        }, timeout=15)
        tok.raise_for_status()
        access = tok.json().get("access_token")
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't reach Gmail. Try again in a moment.")
    if not access:
        raise HTTPException(status_code=400, detail="Gmail authorization expired — reconnect it.")
    try:
        resp = httpx.get(GMAIL_LABELS_URL, headers={"Authorization": f"Bearer {access}"}, timeout=15)
        resp.raise_for_status()
        labels = resp.json().get("labels", [])
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't list your Gmail labels. Try again.")
    return sorted(lab["name"] for lab in labels if lab.get("type") == "user" and lab.get("name"))


@router.get("/email-credentials/folders", response_model=schemas.MailboxFoldersOut)
def list_mailbox_folders(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Live-list the connected mailbox's folders/labels (full names + delimiter) so the
    UI can offer an exact-name picker. Requires a connected mailbox."""
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="No mailbox connected")
    if cred.provider == models.EmailProvider.GMAIL:
        return schemas.MailboxFoldersOut(provider="gmail", delimiter="/", folders=_gmail_list_labels(cred))
    if cred.provider == models.EmailProvider.IMAP:
        delimiter, folders = _imap_list_folders(cred)
        return schemas.MailboxFoldersOut(provider="imap", delimiter=delimiter, folders=folders)
    raise HTTPException(status_code=400, detail="Unsupported mailbox provider.")


@router.put("/email-credentials/imap", response_model=schemas.EmailCredentialStatusOut)
def set_imap_credentials(
    payload: schemas.ImapCredentialsIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Store IMAP mailbox credentials ("Other" provider) after verifying the
    *connection* (host/login) only. Folders are chosen afterwards via the picker
    (GET /agent/email-credentials/folders) — you can't list folders until creds are
    stored. A new mailbox starts disabled until its folders are set + verified (via
    PUT /email-credentials). Encrypted; the password is never returned."""
    # Connection/login check only — passing no folder names skips folder verification.
    _verify_imap(
        payload.host.strip(), payload.port, payload.username.strip(),
        payload.password, payload.use_ssl, [],
    )

    blob = encrypt_api_key(json.dumps({
        "provider": "imap",
        "host": payload.host.strip(),
        "port": payload.port,
        "username": payload.username.strip(),
        "password": payload.password,
        "use_ssl": payload.use_ssl,
    }))
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    if not cred:
        cred = models.EmailCredential(user_id=user.id, enabled=False)
        db.add(cred)
    cred.provider = models.EmailProvider.IMAP
    cred.encrypted_blob = blob
    # Folders are optional here (the picker sets them next); store any provided as-is.
    if payload.folders is not None:
        f = payload.folders
        cred.folder_root = (f.root or "").strip() or None
        cred.folder_interaction = (f.interaction or "").strip() or None
        cred.folder_postings = (f.postings or "").strip() or None
        cred.folder_social = (f.social or "").strip() or None
        cred.folder_unprocessed = (f.unprocessed or "").strip() or None
    db.commit()
    db.refresh(cred)
    return _credential_status(cred)


@router.delete("/email-credentials", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_email_credentials(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Disconnect the mailbox: best-effort revoke at Google, then delete the row."""
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    if cred:
        try:
            rt = json.loads(decrypt_api_key(cred.encrypted_blob)).get("refresh_token")
            if rt:
                httpx.post(GOOGLE_REVOKE_URL, params={"token": rt}, timeout=10)
        except Exception:
            pass  # revoke is best-effort; deletion is what matters
        db.delete(cred)
        db.commit()
    return None


# ── Slack notifications: per-user OAuth install + channel (JR-6) ──

@router.get("/slack/oauth/start")
def slack_oauth_start(user: models.User = Depends(get_current_user)):
    """Return the Slack "Add to Slack" install URL (the frontend redirects to it).
    The install grants a bot token scoped to the user's own workspace."""
    if not (settings.slack_client_id and settings.slack_oauth_redirect_uri):
        raise HTTPException(status_code=503, detail="Slack OAuth is not configured on this server")
    params = {
        "client_id": settings.slack_client_id,
        "scope": settings.slack_bot_scopes,
        "redirect_uri": settings.slack_oauth_redirect_uri,
        "state": create_oauth_state(str(user.id), purpose="slack-oauth"),
    }
    return {"authorization_url": f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"}


@router.get("/slack/oauth/callback")
def slack_oauth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Slack redirects here after install. Exchange the code for the workspace bot
    token, store it (encrypted), bounce back to Settings. Auth = signed `state`."""
    def _back(result: str) -> RedirectResponse:
        return RedirectResponse(url=f"/settings?slack={result}", status_code=303)

    if error or not code or not state:
        return _back("error")
    user_id = decode_oauth_state(state, purpose="slack-oauth")
    if not user_id:
        return _back("error")
    try:
        user_uuid = UUID(user_id)
    except (ValueError, TypeError):
        return _back("error")
    user = db.query(models.User).filter(models.User.id == user_uuid).first()
    if not user or not user.is_approved:
        return _back("error")
    if not (settings.slack_client_id and settings.slack_client_secret and settings.slack_oauth_redirect_uri):
        return _back("error")

    try:
        resp = httpx.post(SLACK_ACCESS_URL, data={
            "client_id": settings.slack_client_id,
            "client_secret": settings.slack_client_secret,
            "code": code,
            "redirect_uri": settings.slack_oauth_redirect_uri,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.error("Slack OAuth token exchange failed user=%s", user_id)
        return _back("error")

    if not data.get("ok") or not data.get("access_token"):
        logger.warning("Slack OAuth not ok user=%s err=%s", user_id, data.get("error"))
        return _back("error")

    team = data.get("team") or {}
    enc = encrypt_api_key(data["access_token"])
    conn = (
        db.query(models.SlackConnection)
        .filter(models.SlackConnection.user_id == user_uuid)
        .first()
    )
    if conn:
        conn.encrypted_bot_token = enc
        conn.team_id = team.get("id")
        conn.team_name = team.get("name")
        conn.bot_user_id = data.get("bot_user_id")
        conn.scopes = data.get("scope")
        # keep the existing channel selection across a re-install
    else:
        db.add(models.SlackConnection(
            user_id=user_uuid, encrypted_bot_token=enc,
            team_id=team.get("id"), team_name=team.get("name"),
            bot_user_id=data.get("bot_user_id"), scopes=data.get("scope"),
        ))
    db.commit()
    return _back("connected")


def _slack_status(conn: Optional[models.SlackConnection]) -> schemas.SlackStatusOut:
    if not conn:
        return schemas.SlackStatusOut(connected=False)
    return schemas.SlackStatusOut(
        connected=True, team_name=conn.team_name,
        channel_id=conn.channel_id, channel_name=conn.channel_name,
    )


@router.get("/slack/status", response_model=schemas.SlackStatusOut)
def slack_status(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Masked Slack connection status for the Settings UI. Never returns the token."""
    conn = db.query(models.SlackConnection).filter(models.SlackConnection.user_id == user.id).first()
    return _slack_status(conn)


@router.get("/slack/channels", response_model=list[schemas.SlackChannelOut])
def slack_channels(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """List the workspace's public channels (for the picker), via the user's bot token."""
    conn = db.query(models.SlackConnection).filter(models.SlackConnection.user_id == user.id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Slack not connected")
    token = decrypt_api_key(conn.encrypted_bot_token)
    try:
        resp = httpx.get(SLACK_CONV_LIST_URL, params={
            "types": "public_channel", "limit": 1000, "exclude_archived": "true",
        }, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't reach Slack")
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=f"Slack error: {data.get('error')}")
    return [
        schemas.SlackChannelOut(id=c["id"], name=c["name"])
        for c in data.get("channels", [])
    ]


@router.put("/slack/channel", response_model=schemas.SlackStatusOut)
def slack_set_channel(
    payload: schemas.SlackChannelUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Choose which channel the agent posts to. Requires a connected workspace."""
    conn = db.query(models.SlackConnection).filter(models.SlackConnection.user_id == user.id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Slack not connected")
    conn.channel_id = payload.channel_id
    conn.channel_name = payload.channel_name
    db.commit()
    db.refresh(conn)
    return _slack_status(conn)


@router.delete("/slack", status_code=status.HTTP_204_NO_CONTENT)
def slack_disconnect(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Disconnect Slack: best-effort token revoke, then delete the connection."""
    conn = db.query(models.SlackConnection).filter(models.SlackConnection.user_id == user.id).first()
    if conn:
        try:
            httpx.post(SLACK_REVOKE_URL, data={"token": decrypt_api_key(conn.encrypted_bot_token)}, timeout=10)
        except Exception:
            pass  # revoke is best-effort; deletion is what matters
        db.delete(conn)
        db.commit()
    return None


@router.get("/reviews", response_model=list[schemas.AgentReviewOut])
def get_agent_reviews(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """Return the user's job reviews for duplicate-detection / matching."""
    rows = (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job))
        .filter(models.UserJobReview.user_id == user.id)
        .all()
    )
    return [
        schemas.AgentReviewOut(
            review_id=r.id,
            company=r.job.company,
            title=r.job.title,
            status=r.status,
            url=r.job.url,
        )
        for r in rows
    ]


@router.post("/inbox", response_model=schemas.AgentInboxOut, status_code=status.HTTP_201_CREATED)
def create_inbox_entry(
    payload: schemas.AgentInboxIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """Create an inbox_email + its postings. Idempotent on (user_id, message_id)."""
    if len(payload.postings) > 30:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Maximum 30 postings per email",
        )

    # Idempotency check
    existing = (
        db.query(models.InboxEmail)
        .options(joinedload(models.InboxEmail.postings))
        .filter(
            models.InboxEmail.user_id == user.id,
            models.InboxEmail.message_id == payload.message_id,
        )
        .first()
    )
    if existing:
        return schemas.AgentInboxOut(
            inbox_email_id=existing.id,
            posting_ids=[p.id for p in existing.postings],
        )

    email = models.InboxEmail(
        user_id=user.id,
        message_id=payload.message_id,
        subject=payload.subject,
        sender=payload.sender,
        received_at=payload.received_at,
        category=payload.category,
        confidence=payload.confidence,
        langfuse_trace_id=payload.langfuse_trace_id,
        raw_extracted_json=payload.raw_extracted_json,
        status=models.EmailStatus.PROCESSED,
    )
    db.add(email)
    db.flush()

    posting_ids = []
    for p in payload.postings:
        link = _validate_link(p.link)  # C2
        posting = models.InboxPosting(
            inbox_email_id=email.id,
            user_id=user.id,
            company=p.company,
            role=p.role,
            link=link,
            action_required=p.action_required,
            possible_duplicate=p.possible_duplicate,
            matched_review_id=p.matched_review_id,
        )
        db.add(posting)
        db.flush()
        posting_ids.append(posting.id)

    db.commit()
    return schemas.AgentInboxOut(inbox_email_id=email.id, posting_ids=posting_ids)


@router.post("/interactions", response_model=schemas.AgentInteractionOut, status_code=status.HTTP_201_CREATED)
def record_interaction(
    payload: schemas.AgentInteractionIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """
    Record an application-status email. If matched_review_id is present and
    new_status is in the agent-writable subset, update the review + timeline
    by reusing the existing PATCH logic (D9/D10).
    """
    if payload.new_status and payload.new_status not in _AGENT_WRITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Agent cannot write status '{payload.new_status.value}'. "
                   f"Writable: {[s.value for s in _AGENT_WRITABLE_STATUSES]}",
        )

    # Create the inbox_email row for this interaction email
    email = models.InboxEmail(
        user_id=user.id,
        message_id=payload.message_id,
        subject=payload.subject,
        sender=payload.sender,
        received_at=payload.received_at,
        category=payload.category,
        confidence=payload.confidence,
        langfuse_trace_id=payload.langfuse_trace_id,
        status=models.EmailStatus.PROCESSED,
    )
    db.add(email)
    db.flush()

    applied_status = None
    needs_review = payload.matched_review_id is None or payload.new_status is None

    if not needs_review:
        # Validate ownership before writing (H1)
        review = _get_review_owned(payload.matched_review_id, user, db)
        if payload.timeline_note:
            note = payload.timeline_note
        elif payload.match_confidence is not None:
            note = f"Status updated by email agent (confidence {payload.match_confidence:.0%})"
        else:
            note = "Status updated by email agent"
        apply_status_change(review, payload.new_status, note, db)
        applied_status = payload.new_status.value

    interaction = models.InboxInteraction(
        inbox_email_id=email.id,
        user_id=user.id,
        matched_review_id=payload.matched_review_id if not needs_review else None,
        match_confidence=payload.match_confidence,
        new_status=payload.new_status,
        applied_at=datetime.now(timezone.utc) if applied_status else None,
    )
    db.add(interaction)

    if needs_review:
        email.status = models.EmailStatus.NEEDS_REVIEW
        email.escalation_reason = "No matched review or unrecognised status"

    db.commit()
    return schemas.AgentInteractionOut(
        interaction_id=interaction.id,
        applied_status=applied_status,
    )


@router.post("/hitl/register")
def register_hitl(
    payload: schemas.AgentHitlRegisterIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """Agent registers a pending HITL decision before posting the Slack prompt."""
    # Validate all candidate review_ids belong to this user (H1)
    for review_id in payload.candidates:
        _get_review_owned(review_id, user, db)

    existing = db.query(models.HitlDecision).filter(
        models.HitlDecision.hitl_id == payload.hitl_id
    ).first()
    if existing:
        return {"ok": True}  # idempotent

    decision = models.HitlDecision(
        user_id=user.id,
        hitl_id=payload.hitl_id,
        status=models.HitlStatus.PENDING,
    )
    db.add(decision)
    db.commit()
    return {"ok": True}


@router.get("/hitl/pending", response_model=list[schemas.HitlDecisionOut])
def get_pending_hitl(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """Return resolved (but not yet consumed) decisions for the polling agent."""
    abandon_cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.hitl_abandon_minutes)

    # Auto-abandon stale pending decisions
    db.query(models.HitlDecision).filter(
        models.HitlDecision.user_id == user.id,
        models.HitlDecision.status == models.HitlStatus.PENDING,
        models.HitlDecision.created_at < abandon_cutoff,
    ).update({"status": models.HitlStatus.ABANDONED})
    db.commit()

    return (
        db.query(models.HitlDecision)
        .filter(
            models.HitlDecision.user_id == user.id,
            models.HitlDecision.status == models.HitlStatus.RESOLVED,
        )
        .all()
    )


@router.post("/hitl/consume")
def consume_hitl(
    payload: schemas.AgentHitlConsumeIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """Mark a resolved decision as consumed after the agent resumes."""
    decision = db.query(models.HitlDecision).filter(
        models.HitlDecision.hitl_id == payload.hitl_id,
        models.HitlDecision.user_id == user.id,  # ownership check (H1)
    ).first()
    if not decision:
        raise HTTPException(status_code=404, detail="HITL decision not found")
    decision.status = models.HitlStatus.ABANDONED  # consumed = no longer pending/resolved
    db.commit()
    return {"ok": True}


@router.post("/hitl/callback")
async def slack_hitl_callback(request: Request, db: Session = Depends(get_db)):
    """
    Slack interactive callback — verifies signature + timestamp (C4),
    then records the user's HITL decision.
    """
    body = await request.body()

    if not settings.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack HITL not configured")

    if not verify_slack_signature(
        signing_secret=settings.slack_signing_secret,
        body=body,
        x_slack_signature=request.headers.get("X-Slack-Signature", ""),
        x_slack_request_timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    # Slack sends interactive payloads as form-encoded JSON in a 'payload' field
    try:
        form = await request.form()
        slack_payload = json.loads(form.get("payload", "{}"))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed Slack payload")

    # Extract hitl_id and choice from the action (treat as untrusted — C4)
    try:
        action = slack_payload["actions"][0]
        hitl_id = action["block_id"]
        choice_value = action["value"]  # review_id UUID string or "none"
    except (KeyError, IndexError):
        raise HTTPException(status_code=400, detail="Unrecognised Slack payload shape")

    decision = db.query(models.HitlDecision).filter(
        models.HitlDecision.hitl_id == hitl_id,
        models.HitlDecision.status == models.HitlStatus.PENDING,
    ).first()
    if not decision:
        return {"ok": True}  # already resolved or abandoned — ack silently

    choice_review_id = None
    if choice_value and choice_value.lower() != "none":
        try:
            rid = UUID(choice_value)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid choice value")
        # Validate choice_review_id belongs to the same user as the decision (C4/H1)
        owned = db.query(models.UserJobReview).filter(
            models.UserJobReview.id == rid,
            models.UserJobReview.user_id == decision.user_id,
        ).first()
        if not owned:
            raise HTTPException(status_code=403, detail="Review does not belong to this user")
        choice_review_id = rid

    decision.status = models.HitlStatus.RESOLVED
    decision.choice_review_id = choice_review_id
    decision.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.post("/runs", response_model=schemas.AgentRunOut, status_code=status.HTTP_201_CREATED)
def report_run(
    payload: schemas.AgentRunIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_agent_writer),
):
    """Operational heartbeat — counts only, no email content (H2)."""
    run = models.AgentRun(
        user_id=user.id,
        environment=payload.environment,
        agent_version=payload.agent_version,
        status=payload.status,
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        emails_processed=payload.emails_processed,
        postings_created=payload.postings_created,
        interactions_recorded=payload.interactions_recorded,
        escalations=payload.escalations,
        retries=payload.retries,
        error_summary=payload.error_summary,
    )
    db.add(run)
    db.commit()
    return schemas.AgentRunOut(run_id=run.id)


# ── Frontend-facing endpoints (JWT) ──────────────────────────
# Literal routes registered before /{id} param route.

@router.get("/inbox", response_model=schemas.PaginatedInbox)
def list_inbox(
    email_status: Optional[models.EmailStatus] = Query(None, alias="status"),
    category: Optional[models.EmailCategory] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = (
        db.query(models.InboxEmail)
        .options(
            joinedload(models.InboxEmail.postings),
            joinedload(models.InboxEmail.interactions),
        )
        .filter(models.InboxEmail.user_id == current_user.id)
    )
    if email_status:
        q = q.filter(models.InboxEmail.status == email_status)
    if category:
        q = q.filter(models.InboxEmail.category == category)

    total = q.count()
    items = q.order_by(models.InboxEmail.received_at.desc()).offset(skip).limit(limit).all()
    return schemas.PaginatedInbox(total=total, items=items)


@router.get("/stats", response_model=schemas.AgentStatsOut)
def get_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Per-user agent stats for the settings page."""
    return _compute_stats(current_user.id, db)


@router.get("/stats/global", response_model=schemas.AgentStatsOut)
def get_stats_global(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(get_current_admin),
):
    """Global agent stats for the admin dashboard."""
    return _compute_stats(None, db)


def _compute_stats(user_id: Optional[UUID], db: Session) -> schemas.AgentStatsOut:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    base = db.query(models.InboxEmail)
    if user_id:
        base = base.filter(models.InboxEmail.user_id == user_id)

    emails_today = base.filter(models.InboxEmail.created_at >= today_start).count()
    emails_week = base.filter(models.InboxEmail.created_at >= week_start).count()

    total = base.count()
    breakdown = {}
    for cat in models.EmailCategory:
        n = base.filter(models.InboxEmail.category == cat).count()
        breakdown[cat.value] = n

    needs_review = base.filter(models.InboxEmail.status == models.EmailStatus.NEEDS_REVIEW).count()
    escalation_rate = (needs_review / total) if total > 0 else 0.0

    postings_q = db.query(models.InboxPosting).filter(
        models.InboxPosting.import_status == models.ImportStatus.IMPORTED
    )
    if user_id:
        postings_q = postings_q.filter(models.InboxPosting.user_id == user_id)
    jobs_imported = postings_q.count()

    runs_q = db.query(models.AgentRun)
    if user_id:
        runs_q = runs_q.filter(models.AgentRun.user_id == user_id)
    last_run = runs_q.order_by(models.AgentRun.finished_at.desc().nullslast()).first()
    last_run_out = (
        schemas.AgentLastRunOut(
            run_id=last_run.id,   # model PK is `id`; schema field is `run_id`
            status=last_run.status,
            finished_at=last_run.finished_at,
            emails_processed=last_run.emails_processed,
            environment=last_run.environment,
        )
        if last_run else None
    )

    return schemas.AgentStatsOut(
        emails_today=emails_today,
        emails_this_week=emails_week,
        category_breakdown=breakdown,
        escalation_rate=round(escalation_rate, 4),
        jobs_imported=jobs_imported,
        last_run=last_run_out,
    )


# ── /{id} param routes last (route ordering) ─────────────────

@router.patch("/inbox/{inbox_id}", response_model=schemas.InboxEmailOut)
def update_inbox_entry(
    inbox_id: UUID,
    payload: schemas.InboxEmailUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    row = _get_inbox_email_or_404(inbox_id, current_user, db)
    if payload.status is not None:
        row.status = payload.status
    db.commit()
    db.refresh(row)
    return row


@router.delete("/inbox/{inbox_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inbox_entry(
    inbox_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Delete an inbox entry. Cascades to postings + interactions.
    Mirrors the last-review-deletes-Job pattern: if this was the last posting
    for the parent email, the email row is also deleted (handled by cascade).
    """
    row = _get_inbox_email_or_404(inbox_id, current_user, db)
    db.delete(row)
    db.commit()


# ── Agent API key management (admin-managed, user-scoped) ─────

@router.post("/keys", response_model=schemas.AgentAPIKeyCreatedOut, status_code=status.HTTP_201_CREATED)
def create_agent_key(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a new agent API key for the current user. Raw key shown once."""
    raw, key_hash, key_hint = generate_agent_key()
    key_row = models.AgentAPIKey(
        user_id=current_user.id,
        key_hash=key_hash,
        key_hint=key_hint,
    )
    db.add(key_row)
    db.commit()
    db.refresh(key_row)
    return schemas.AgentAPIKeyCreatedOut(
        id=key_row.id,
        key_hint=key_hint,
        created_at=key_row.created_at,
        last_used_at=key_row.last_used_at,
        revoked=key_row.revoked,
        raw_key=raw,
    )


@router.get("/keys", response_model=list[schemas.AgentAPIKeyOut])
def list_agent_keys(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.AgentAPIKey)
        .filter(models.AgentAPIKey.user_id == current_user.id)
        .order_by(models.AgentAPIKey.created_at.desc())
        .all()
    )


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_agent_key(
    key_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    key_row = db.query(models.AgentAPIKey).filter(
        models.AgentAPIKey.id == key_id,
        models.AgentAPIKey.user_id == current_user.id,  # ownership (H1)
    ).first()
    if not key_row:
        raise HTTPException(status_code=404, detail="Key not found")
    key_row.revoked = True
    db.commit()
