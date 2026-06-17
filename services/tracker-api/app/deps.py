"""
FastAPI dependency injection helpers.
"""

import hmac
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AgentAPIKey, User
from app.security import decode_access_token, hash_agent_key


def get_current_user(
    access_token: str = Cookie(default=None),
    x_internal_user_id: str = Header(default=None, alias="X-Internal-User-Id"),
    db: Session = Depends(get_db),
) -> User:
    """Require a valid JWT cookie. Raises 401 if missing or invalid."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Internal service-to-service calls bypass JWT (ai-reviewer, scraper)
    if x_internal_user_id:
        user = db.query(User).filter(User.id == x_internal_user_id).first()
        if user and user.is_approved:
            return user
        raise credentials_exception

    if not access_token:
        raise credentials_exception

    user_id = decode_access_token(access_token)
    if not user_id:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise credentials_exception
    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval",
        )
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require the current user to be an admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def _user_from_agent_key(x_agent_key: str, db: Session) -> User:
    """Derive user from an agent API key (H1). Never trust user_id from the request."""
    key_hash = hash_agent_key(x_agent_key)
    key_row = (
        db.query(AgentAPIKey)
        .filter(AgentAPIKey.key_hash == key_hash, AgentAPIKey.revoked == False)  # noqa: E712
        .first()
    )
    if not key_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent key")

    key_row.last_used_at = datetime.now(timezone.utc)
    db.flush()

    user = db.query(User).filter(User.id == key_row.user_id).first()
    if not user or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not approved")
    return user


def get_user_from_agent_key(
    x_agent_key: str = Header(default=None, alias="X-Agent-Key"),
    db: Session = Depends(get_db),
) -> User:
    """Require a valid agent API key and return its user."""
    if not x_agent_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent key required")
    return _user_from_agent_key(x_agent_key, db)


def _valid_internal_token(token: str | None) -> bool:
    """Constant-time check against the cloud agent internal token. Fail-closed:
    when the token is unconfigured, no request can satisfy it (JR-5)."""
    if not settings.agent_internal_token or not token:
        return False
    return hmac.compare_digest(token, settings.agent_internal_token)


def require_internal_token(
    x_internal_token: str = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Gate the in-cluster /agent/cloud/* enumeration endpoints. The cloud agent
    authenticates as *itself* (not a user). In-cluster only — also blocked at nginx
    and behind the NetworkPolicy, same posture as /agent/config."""
    if not _valid_internal_token(x_internal_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


def get_agent_writer(
    x_agent_key: str = Header(default=None, alias="X-Agent-Key"),
    x_internal_token: str = Header(default=None, alias="X-Internal-Token"),
    x_agent_user_id: str = Header(default=None, alias="X-Agent-User-Id"),
    db: Session = Depends(get_db),
) -> User:
    """Dual-mode auth for the agent write/poll endpoints (JR-5 §2.1b):
      • X-Agent-Key                       → local self-host path; user derived from key.
      • X-Internal-Token + X-Agent-User-Id → cloud path; the single CronJob writes on
        behalf of each user. user_id is trustworthy *because* the caller holds the
        internal token behind the NetworkPolicy.
    """
    if x_agent_key:
        return _user_from_agent_key(x_agent_key, db)

    if x_internal_token is not None:
        if not _valid_internal_token(x_internal_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")
        if not x_agent_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Agent-User-Id required with X-Internal-Token",
            )
        try:
            user_uuid = UUID(x_agent_user_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed X-Agent-User-Id")
        user = db.query(User).filter(User.id == user_uuid).first()
        if not user or not user.is_approved:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not approved")
        return user

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent key or internal token required")
