"""One log format across every service, so the hourly digest can parse them.

Before this existed, tracker-api had no logging configuration at all: Python's
`lastResort` handler emitted the bare message with no level, no timestamp and no
logger name, and suppressed INFO entirely. Nothing downstream could tell an error
from a routine line. Celery, meanwhile, imposed its own different format on the
two workers.

The format is fixed rather than configurable because a parser reads it:

    2026-08-01 04:29:02,042 WARNING app.reviewer: LLM call failed for job …

Changing it means changing `log_digest.LOG_LINE`.
"""

import logging
import re

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ── Credential redaction ─────────────────────────────────────────────────
# BYOK means user API keys ride through this code, and they leak into logs
# through paths we don't control: litellm surfaces Google's 429 as a raw httpx
# error whose URL is `…:generateContent?key=AIzaSy…` (Google authenticates via
# query param), and logger.exception prints the traceback — which is how a
# user's key reached the pod logs AND the hourly digest email on 2026-09-03.
# The formatter is the one chokepoint that covers tracebacks, message text and
# args alike, whoever does the logging; per-call-site redaction would be a
# whack-a-mole we lose the first time someone adds a log line.

_REDACT_PATTERNS = [
    # Credential-bearing query params (Google `key=`, Adzuna `app_key=`, …).
    re.compile(r"(?i)([?&](?:api_?key|key|token|access_token|app_key|app_id)=)[^&\s'\"]+"),
    # Bare key material by shape, wherever it appears: Google (AIza…),
    # OpenAI/Anthropic (sk-…), Groq (gsk_…). The prefix survives so a redacted
    # log line still says which provider's key it was.
    re.compile(r"\b(AIza)[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\b(sk-)[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(gsk_)[A-Za-z0-9_\-]{16,}"),
]


def redact(text: str) -> str:
    """Strip credential material from text bound for logs, email or the DB."""
    for pat in _REDACT_PATTERNS:
        text = pat.sub(r"\1<redacted>", text)
    return text


class _RedactingFormatter(logging.Formatter):
    """LOG_FORMAT unchanged — the digest parser sees the same shape, minus keys."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))

# Libraries that are chatty at INFO and say nothing an operator wants. uvicorn's
# access log in particular would be one line per request — it would bury real
# output and, since nginx already logs requests, tell us nothing new.
_QUIET = {
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "botocore": logging.WARNING,
    "boto3": logging.WARNING,
    "urllib3": logging.WARNING,
}


def configure_logging(level: str = "INFO") -> None:
    """Install the shared format on the root logger.

    `force=True` replaces any handler already installed — uvicorn and Celery both
    configure logging on startup, and without this the service silently keeps
    their format instead of ours.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(_RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )
    for name, lvl in _QUIET.items():
        logging.getLogger(name).setLevel(lvl)


# Celery installs its own root-logger handler on worker startup, which would
# replace this format after the module-level call runs. `worker_hijack_root_logger
# = False` on the app (see main.py) is what stops it — without it, this file has
# no effect in a worker and the digest silently misses everything the worker logs.
