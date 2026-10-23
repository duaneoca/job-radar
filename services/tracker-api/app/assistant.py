"""What a connected AI assistant may do to a user's account, and how much.

Enforced HERE, in tracker-api, not in the MCP connector: a bearer token can be
used by any client, and the safety rules must hold whichever one it is.

  * Writes through PATCH /jobs/{id}: star / unstar, or set the status to
    EXPIRED or DISMISSED with a reason. Nothing else — never applied,
    interviewing, offer… (a wrong "applied" is the mistake the user already
    made twice by hand); never notes (use the append-only endpoint, so an
    assistant can add to them but never wipe them).
  * Daily limits, rolling 24 h, per account (assistant actions only — the
    user's own clicks are never counted): research 20, tailoring 10,
    expire + dismiss together 25. Expired and dismissed jobs are deleted by
    cleanup after 14 days unless starred, which is why that cap matters most.
  * Every action leaves a timeline entry naming the assistant.

Limits are fixed defaults for now (user's decision 2026-10-23); revisit if
they get in the way.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app import models, oauth

ACTION_RESEARCH = "research"
ACTION_TAILOR = "tailor"
ACTION_REMOVE = "remove"          # expire or dismiss

LIMITS = {ACTION_RESEARCH: 20, ACTION_TAILOR: 10, ACTION_REMOVE: 25}
WINDOW = timedelta(hours=24)

_LABEL = {ACTION_RESEARCH: "company research runs", ACTION_TAILOR: "résumé tailoring runs",
          ACTION_REMOVE: "jobs expired or dismissed"}

ASSISTANT_STATUSES = {models.JobStatus.EXPIRED, models.JobStatus.DISMISSED}
ASSISTANT_PATCH_FIELDS = {"starred", "status", "status_reason"}


def is_assistant(request: Request) -> bool:
    return oauth.actor(request) != models.GENERATION_SOURCE_WEB


def who(request: Request) -> str:
    """'Claude (Anthropic)' etc. — for timeline text."""
    return oauth.VENDORS.get(oauth.actor(request), oauth.actor(request))


def used(db: Session, user_id, action: str) -> int:
    since = datetime.now(timezone.utc) - WINDOW
    return (db.query(models.AssistantAction)
              .filter(models.AssistantAction.user_id == user_id,
                      models.AssistantAction.action == action,
                      models.AssistantAction.created_at >= since)
              .count())


def check(db: Session, user_id, action: str) -> None:
    """429 with a plain message once the day's limit is reached."""
    if used(db, user_id, action) >= LIMITS[action]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(f"Daily limit reached: connected apps can start {LIMITS[action]} "
                    f"{_LABEL[action]} per day on this account. The user can still do it "
                    "in Job Radar; the limit resets over the next 24 hours."),
        )


def record(db: Session, request: Request, user_id, action: str, review_id=None) -> None:
    db.add(models.AssistantAction(
        user_id=user_id, grant_id=getattr(request.state, "oauth_grant_id", None),
        vendor=oauth.actor(request), action=action, review_id=review_id,
    ))


def usage(db: Session, user_id) -> dict:
    return {a: {"used": used(db, user_id, a), "limit": lim} for a, lim in LIMITS.items()}


def guard_patch(update: dict) -> None:
    """403 for anything an assistant may not change through PATCH."""
    extra = set(update) - ASSISTANT_PATCH_FIELDS
    if extra:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("Connected apps can only star a job, or mark it expired or dismissed. "
                    f"Not allowed: {', '.join(sorted(extra))}."),
        )
    if "status" in update:
        if update["status"] not in ASSISTANT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connected apps can only set a job's status to expired or dismissed.",
            )
        if not (update.get("status_reason") or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Say why (status_reason) when expiring or dismissing a job — it goes in the job's timeline.",
            )
