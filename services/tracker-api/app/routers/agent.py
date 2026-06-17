"""
Agent router — /agent/* endpoints.

Three auth classes:
  • Agent-facing   (X-Agent-Key header) — user derived from key (H1)
  • Frontend-facing (JWT cookie)        — existing get_current_user
  • Slack-facing   (signing-secret)     — HITL callback (C4)

Route ordering: all literal paths registered before /{id} param routes.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.config import settings
from app.database import get_db
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
    # LLM key — reuse existing user_api_keys (§1.9: do not build new)
    llm_config = None
    key_row = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == user.id,
            models.UserAPIKey.provider.in_(models.LLM_PROVIDERS),  # exclude tavily/adzuna
        )
        .order_by(models.UserAPIKey.updated_at.desc())
        .first()
    )
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

    return schemas.AgentConfigOut(
        provider=email_provider,
        folders=folders,
        llm=llm_config,
        email_credentials=email_credentials_blob,
        enabled=bool(cred and cred.enabled),
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
            enabled=True,
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
    connected = False
    try:
        connected = bool(json.loads(decrypt_api_key(cred.encrypted_blob)).get("refresh_token"))
    except Exception:
        connected = False
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
    """Set folder/label config and the enable flag. Requires a connected mailbox
    (the row is created by the OAuth callback, which supplies the secret blob)."""
    cred = (
        db.query(models.EmailCredential)
        .filter(models.EmailCredential.user_id == user.id)
        .first()
    )
    if not cred:
        raise HTTPException(status_code=404, detail="No mailbox connected")
    f = payload.folders
    cred.folder_root = f.root
    cred.folder_interaction = f.interaction
    cred.folder_postings = f.postings
    cred.folder_social = f.social
    cred.folder_unprocessed = f.unprocessed
    cred.enabled = payload.enabled
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
        note = payload.timeline_note or f"Status updated by email agent (confidence {payload.match_confidence:.0%})"
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
