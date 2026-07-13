"""Global admin-controlled feature flags, backed by the app_settings KV table.

Flags default at the code level when their row is absent — no seed rows, no
migration per flag. Reads are a single PK lookup; callers already hold a
Session, so there is no caching layer (the table is tiny and rarely written).
"""
from sqlalchemy.orm import Session

from . import models

EMAIL_AGENT_ENABLED = "email_agent_enabled"

# Code-level defaults for absent rows. email_agent is OFF by default: the
# feature is hidden/disabled until an admin explicitly enables it.
_DEFAULTS: dict[str, bool] = {
    EMAIL_AGENT_ENABLED: False,
}


def _get(db: Session, key: str) -> bool:
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    if row is None:
        return _DEFAULTS[key]
    return bool(row.value)


def _set(db: Session, key: str, value: bool) -> None:
    row = db.query(models.AppSetting).filter(models.AppSetting.key == key).first()
    if row is None:
        db.add(models.AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def email_agent_enabled(db: Session) -> bool:
    return _get(db, EMAIL_AGENT_ENABLED)


def set_email_agent_enabled(db: Session, enabled: bool) -> None:
    _set(db, EMAIL_AGENT_ENABLED, enabled)
