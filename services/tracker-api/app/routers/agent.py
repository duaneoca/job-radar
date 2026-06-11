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
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_admin, get_current_user, get_user_from_agent_key
from app.routers.jobs import apply_status_change
from app.security import decrypt_api_key, generate_agent_key, hash_agent_key, verify_slack_signature

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

@router.get("/config")
def get_agent_config(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_user_from_agent_key),
):
    """
    Return decrypted LLM key + email credentials for the agent (H6/H6a).
    MUST be called in-cluster only — NetworkPolicy blocks external access (JR-5).
    Every call is audit-logged here as defense-in-depth.
    """
    logger.warning(
        "AUDIT /agent/config user=%s ip=%s",
        user.id,
        request.client.host if request.client else "unknown",
    )

    # LLM key — reuse existing user_api_keys (§1.9: do not build new)
    llm_config = None
    key_row = (
        db.query(models.UserAPIKey)
        .filter(models.UserAPIKey.user_id == user.id)
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
        try:
            email_credentials_blob = json.loads(decrypt_api_key(cred.encrypted_blob))
        except Exception:
            logger.error("AUDIT /agent/config email cred decrypt failed user=%s", user.id)
        email_provider = cred.provider.value
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
    )


@router.get("/reviews", response_model=list[schemas.AgentReviewOut])
def get_agent_reviews(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_user_from_agent_key),
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
    user: models.User = Depends(get_user_from_agent_key),
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
    user: models.User = Depends(get_user_from_agent_key),
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
    user: models.User = Depends(get_user_from_agent_key),
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
    user: models.User = Depends(get_user_from_agent_key),
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
    user: models.User = Depends(get_user_from_agent_key),
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
    user: models.User = Depends(get_user_from_agent_key),
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

    return schemas.AgentStatsOut(
        emails_today=emails_today,
        emails_this_week=emails_week,
        category_breakdown=breakdown,
        escalation_rate=round(escalation_rate, 4),
        jobs_imported=jobs_imported,
        last_run=last_run,
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
