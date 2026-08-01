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

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

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
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        force=True,
    )
    for name, lvl in _QUIET.items():
        logging.getLogger(name).setLevel(lvl)
