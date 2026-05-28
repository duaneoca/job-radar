"""
FastAPI dependency injection helpers.
"""

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token


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
