"""
SES email helpers.

All functions silently no-op if SES credentials / from-address are not
configured, so the app works fine in dev without AWS set up.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _ses_client():
    """Return a boto3 SES client, or None if ses_from_email is not configured.

    Credentials are resolved by boto3's standard chain:
      1. Explicit env vars (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) if set
      2. EC2 instance IAM role (preferred — no keys to manage)
    """
    if not settings.ses_from_email:
        return None
    try:
        import boto3
        kwargs = {"region_name": settings.ses_region}
        if settings.aws_access_key_id:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        return boto3.client("ses", **kwargs)
    except Exception as exc:
        logger.warning("Could not create SES client: %s", exc)
        return None


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns False (never raises) when SES isn't
    configured or the send fails — no caller here is important enough to crash
    over a mail failure."""
    if not to:
        return False
    client = _ses_client()
    if client is None:
        logger.info("SES not configured — skipping email %r to %s", subject, to)
        return False
    try:
        client.send_email(
            Source=settings.ses_from_email,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        return True
    except Exception as exc:
        logger.error("SES send_email failed (%r → %s): %s", subject, to, exc)
        return False


def notify_new_account(email: str, full_name: str | None = None) -> None:
    """Send admin notification when a new account signup is pending approval."""
    if not settings.admin_notify_email:
        return

    name_line = f"Name:  {full_name}" if full_name else "Name:  (not provided)"
    body = (
        f"A new account is waiting for approval on Job Radar.\n\n"
        f"Email: {email}\n"
        f"{name_line}\n\n"
        f"Approve or reject at:\n"
        f"https://job-radar.net/admin\n"
    )
    if send_email(settings.admin_notify_email,
                  f"Job Radar: new account request from {email}", body):
        logger.info("Sent new-account notification for %s → %s", email, settings.admin_notify_email)


def notify_account_approved(email: str, full_name: str | None = None) -> None:
    """Notify a user that their account has been approved."""
    name = full_name or email
    body = (
        f"Hi {name},\n\n"
        f"Your Job Radar account has been approved. You can now log in at:\n"
        f"https://job-radar.net\n\n"
        f"— Job Radar\n"
    )
    if send_email(email, "Your Job Radar account is approved", body):
        logger.info("Sent approval notification to %s", email)
