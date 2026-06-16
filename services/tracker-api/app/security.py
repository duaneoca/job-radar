"""
Security utilities — JWT, password hashing, and API key encryption.

API key encryption uses Fernet (AES-128-CBC + HMAC-SHA256).  Key selection:
  • ENCRYPTION_KEY set           → use it directly as the Fernet key
  • ENCRYPTION_KEY + OLD set     → MultiFernet([new, old]) — supports rotation
  • Neither set                  → derive from SECRET_KEY via SHA-256 (legacy)

The plaintext key is never written to the DB.
"""

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from cryptography.fernet import Fernet, MultiFernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# ── Password hashing ──────────────────────────────────────────

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── JWT ───────────────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[str]:
    """Return user_id string on success, None on any failure."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload.get("sub")
    except JWTError:
        return None


# ── OAuth state (CSRF + user binding for Gmail connect, JR-5) ──

def create_oauth_state(user_id: str, minutes: int = 15) -> str:
    """Short-lived signed state binding the consent redirect to a user."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload = {"sub": user_id, "scope": "gmail-oauth", "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_oauth_state(state: str) -> Optional[str]:
    """Return user_id from a valid, unexpired gmail-oauth state token, else None."""
    try:
        payload = jwt.decode(
            state, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if payload.get("scope") != "gmail-oauth":
        return None
    return payload.get("sub")


# ── API key encryption ────────────────────────────────────────

def _legacy_fernet_key() -> bytes:
    """Derive a Fernet key from SECRET_KEY (SHA-256 + base64url). Legacy path."""
    raw = hashlib.sha256(settings.secret_key.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _fernet() -> Union[Fernet, MultiFernet]:
    """Return the active encryption object based on current config."""
    if settings.encryption_key:
        primary = Fernet(settings.encryption_key)
        if settings.encryption_key_old:
            secondary = Fernet(settings.encryption_key_old)
            return MultiFernet([primary, secondary])
        return primary
    # backward compat: derive from SECRET_KEY
    return Fernet(_legacy_fernet_key())


def encrypt_api_key(plaintext: str) -> str:
    """Return base64-encoded ciphertext safe to store in the DB."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a stored API key back to plaintext."""
    return _fernet().decrypt(ciphertext.encode()).decode()


# ── Agent API key helpers ─────────────────────────────────────

def generate_agent_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, key_hint). Store only hash+hint."""
    raw = "jr_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    key_hint = raw[-4:]
    return raw, key_hash, key_hint


def hash_agent_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Slack request verification (C4) ──────────────────────────

_SLACK_MAX_SKEW_SECONDS = 300


def verify_slack_signature(
    signing_secret: str,
    body: bytes,
    x_slack_signature: str,
    x_slack_request_timestamp: str,
) -> bool:
    """Return True iff the Slack signature is valid and timestamp is fresh."""
    try:
        ts = int(x_slack_request_timestamp)
    except (ValueError, TypeError):
        return False

    if abs(time.time() - ts) > _SLACK_MAX_SKEW_SECONDS:
        return False

    sig_base = f"v0:{ts}:{body.decode('utf-8', errors='replace')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(),
        sig_base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, x_slack_signature)
