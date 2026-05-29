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


def notify_new_account(email: str, full_name: str | None = None) -> None:
    """Send admin notification when a new account signup is pending approval."""
    if not settings.admin_notify_email:
        return
    client = _ses_client()
    if client is None:
        logger.info("SES not configured — skipping new-account notification for %s", email)
        return

    name_line = f"Name:  {full_name}" if full_name else "Name:  (not provided)"
    body = (
        f"A new account is waiting for approval on Job Radar.\n\n"
        f"Email: {email}\n"
        f"{name_line}\n\n"
        f"Approve or reject at:\n"
        f"https://jobradar.duanesworld.org/admin\n"
    )
    try:
        client.send_email(
            Source=settings.ses_from_email,
            Destination={"ToAddresses": [settings.admin_notify_email]},
            Message={
                "Subject": {"Data": f"Job Radar: new account request from {email}"},
                "Body": {"Text": {"Data": body}},
            },
        )
        logger.info("Sent new-account notification for %s → %s", email, settings.admin_notify_email)
    except Exception as exc:
        # Never crash the signup flow because of an email failure
        logger.error("SES send_email failed: %s", exc)


def notify_account_approved(email: str, full_name: str | None = None) -> None:
    """Notify a user that their account has been approved."""
    client = _ses_client()
    if client is None:
        return

    name = full_name or email
    body = (
        f"Hi {name},\n\n"
        f"Your Job Radar account has been approved. You can now log in at:\n"
        f"https://jobradar.duanesworld.org\n\n"
        f"— Job Radar\n"
    )
    try:
        client.send_email(
            Source=settings.ses_from_email,
            Destination={"ToAddresses": [email]},
            Message={
                "Subject": {"Data": "Your Job Radar account is approved"},
                "Body": {"Text": {"Data": body}},
            },
        )
        logger.info("Sent approval notification to %s", email)
    except Exception as exc:
        logger.error("SES send_email (approval) failed: %s", exc)
