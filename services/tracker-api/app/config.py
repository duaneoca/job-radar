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
    # Reap agent_runs left with finished_at NULL this many minutes after start
    # (§1.6b). The agent finalizes on crash/SIGTERM; only a hard SIGKILL/OOM can
    # dangle, so this is the safety net. Well beyond the CronJob's ~10-min deadline.
    agent_run_reap_minutes: int = 30

    # Cloud agent internal token (JR-5). The single in-cluster CronJob authenticates
    # as *itself* (not a user) to the /agent/cloud/* enumeration endpoints, and uses
    # this token + an explicit user_id to write back on behalf of each user. Must
    # match AGENT_INTERNAL_TOKEN in email-agent-secrets. Unset = cloud path disabled
    # (fail-closed). Never reachable externally (nginx 404 + NetworkPolicy).
    agent_internal_token: str = ""

    # Gmail OAuth — cloud mailbox users (JR-5). One shared Web-application client;
    # only the per-user refresh_token (+scopes) is stored in email_credentials.
    # client_id/secret/token_uri are injected into /agent/config at read time and
    # are never persisted per-user. The Desktop client used by the local
    # self-host path (gmail_auth.py) is separate from this Web client.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""  # e.g. https://job-radar.net/api/agent/oauth/callback
    gmail_oauth_scopes: str = "https://www.googleapis.com/auth/gmail.modify"

    # Slack OAuth — per-user, per-workspace notifications. One shared Slack app
    # (public distribution); the "Add to Slack" install returns a bot token scoped
    # to each user's own workspace, stored encrypted in slack_connections. The
    # decrypted token + channel are injected into /agent/config at read time.
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_oauth_redirect_uri: str = ""  # e.g. https://job-radar.net/api/agent/slack/oauth/callback
    slack_bot_scopes: str = "chat:write,chat:write.public,channels:read"

    # CORS — comma-separated allowed origins. Dev default is the Vite dev server.
    # In production the SPA is same-origin (served by nginx, /api proxied), so this
    # only needs a value if a cross-origin client is ever added.
    cors_origins: str = "http://localhost:3000"

    class Config:
        env_file = ".env"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
