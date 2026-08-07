"""Log level for a failed outbound fetch.

Every scraper wrapped its per-item fetch in `except Exception: logger.exception(...)`,
which is ERROR. That made the level depend on HOW a third party failed rather than
on whether anyone can act: Adzuna answering 503 was a WARNING (the code checks the
status), while Adzuna not answering at all was an ERROR (the timeout raised). Same
outage, same nothing-to-do-about-it, two different levels — and only one of them
reached the operator's hourly digest.

Measured over 48h in production: 117 × 503, 1 × 429, 1 × 500 and a single read
timeout, while Adzuna still delivered ~1,000 jobs a day. Nothing was broken. One
transient hiccup produced an error report.

The rule is the same one the ai-reviewer uses: the level follows who can fix it.
A provider being unreachable is nobody's bug. An unexpected exception here is
ours, and stays ERROR.
"""

import httpx

# httpx.TransportError covers the "request never completed" family — timeouts,
# connection failures, protocol errors. Deliberately the base class rather than a
# list of leaves, so a new subclass doesn't quietly fall through to ERROR.
_UNREACHABLE = (httpx.TransportError,)

# Status codes that mean "try later", not "you asked wrongly".
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def is_transient(exc: BaseException) -> bool:
    """Whether this failure is the third party's weather rather than our bug."""
    if isinstance(exc, _UNREACHABLE):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response is not None and exc.response.status_code in _RETRYABLE_STATUS
    return False


def log_fetch_failure(logger, exc: BaseException, msg: str, *args) -> None:
    """WARNING when the provider was unreachable, ERROR when we broke.

    ERROR keeps the traceback, because an unexpected exception is the case where
    someone needs the stack. A transient one gets a single line — 117 tracebacks
    for a flaky upstream is how a digest becomes unreadable.
    """
    if is_transient(exc):
        logger.warning("%s: %s", msg % args if args else msg, exc)
    else:
        logger.exception(msg, *args)
