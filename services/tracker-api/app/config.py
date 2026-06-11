from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://jobradar:jobradar_dev@localhost:5432/jobradar"
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "development"

    # JWT — sign tokens with this secret (keep it long and random in production)
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    # Fernet encryption for stored API keys.
    # When set, ENCRYPTION_KEY is used directly as the Fernet key (32-byte
    # URL-safe base64).  When unset, a key is derived from SECRET_KEY via
    # SHA-256 (backward-compatible with deployments predating this split).
    # Set ENCRYPTION_KEY_OLD during rotation: the app will try the new key
    # first, then fall back to the old one so all rows decrypt while the
    # re-encrypt script runs.
    encryption_key: str = ""
    encryption_key_old: str = ""   # previous Fernet key, kept only during rotation

    # Bootstrap admin — on startup, if no users exist and these are set,
    # the admin account is created automatically.
    admin_email: str = ""
    admin_password: str = "changeme123"   # forced change on first login

    # Job pool — unactioned jobs older than this many days are soft-expired.
    job_ttl_days: int = 30
    # Terminal-status reviews (dismissed / rejected / expired) older than this
    # many days are hard-deleted along with any jobs that become orphaned.
    terminal_ttl_days: int = 14

    # AWS SES — email notifications (all optional; notifications silently skipped if unset)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    ses_region: str = "us-east-1"
    ses_from_email: str = ""       # must be verified in SES, e.g. "noreply@job-radar.net"
    admin_notify_email: str = ""   # where new-account alerts go, e.g. "duaneo@duanesworld.org"

    # Email agent
    slack_signing_secret: str = ""  # Slack app signing secret for HITL callbacks (C4)
    hitl_abandon_minutes: int = 30  # abandon pending HITL decisions older than this

    class Config:
        env_file = ".env"


settings = Settings()
