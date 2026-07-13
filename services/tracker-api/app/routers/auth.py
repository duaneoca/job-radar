"""
Auth router — signup, login, logout, me, change-password.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app import feature_flags, models, ratelimit, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.email import notify_new_account
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _with_flags(user: models.User, db: Session) -> models.User:
    """Attach global feature flags to the ORM user so UserOut picks them up
    (email_agent_enabled is app-wide state, not a users column)."""
    user.email_agent_enabled = feature_flags.email_agent_enabled(db)
    return user

_COOKIE = "access_token"
# Secure flag is on in production (HTTPS via Cloudflare) so the session cookie is
# never sent over plaintext HTTP; off in dev where the app runs on http://localhost.
_COOKIE_OPTS = dict(
    httponly=True,
    samesite="lax",
    secure=settings.environment == "production",
)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: schemas.SignupRequest, db: Session = Depends(get_db)):
    """
    Create a new account.  The account starts unapproved — an admin must
    approve it before the user can log in.
    """
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = models.User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        is_approved=False,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    notify_new_account(user.email, user.full_name)

    return {"message": "Account created. Awaiting admin approval."}


@router.post("/login", response_model=schemas.TokenOut)
def login(
    payload: schemas.LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    rl_key = ratelimit.client_ip(request.headers, request.client)
    if not ratelimit.is_allowed(rl_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        ratelimit.record_failure(rl_key)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Correct credentials — not a brute-force attempt; clear the counter even if
    # the account isn't approved yet.
    ratelimit.reset(rl_key)
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Account pending approval")

    token = create_access_token(str(user.id))
    response.set_cookie(key=_COOKIE, value=token, **_COOKIE_OPTS)

    return schemas.TokenOut(access_token=token, user=_with_flags(user, db))


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(_COOKIE)
    return {"message": "Logged out"}


@router.get("/me", response_model=schemas.UserOut)
def me(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _with_flags(current_user, db)


@router.post("/change-password")
def change_password(
    payload: schemas.ChangePasswordRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    db.commit()
    return {"message": "Password updated"}


@router.patch("/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.UpdateProfileRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the current user's own profile fields (name, etc.)."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    db.commit()
    db.refresh(current_user)
    return _with_flags(current_user, db)
