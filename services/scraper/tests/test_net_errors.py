"""Which upstream failures deserve an operator's attention.

Adzuna answering 503 was a WARNING (the code checks the status); Adzuna not
answering at all was an ERROR (the timeout raised into logger.exception). Same
outage, same nothing-to-do-about-it, and only one of them reached the hourly
digest. Over 48h that was 117 × 503 logged quietly and a single read timeout
logged as a fault — while Adzuna delivered ~1,000 jobs a day and nothing was
actually wrong.
"""

import logging

import httpx
import pytest

from app.net_errors import is_transient, log_fetch_failure


@pytest.mark.parametrize("exc", [
    httpx.ConnectTimeout("timed out"),
    httpx.ReadTimeout("timed out"),
    httpx.PoolTimeout("timed out"),
    httpx.ConnectError("refused"),
    httpx.ReadError("reset"),
    httpx.RemoteProtocolError("bad chunk"),
])
def test_unreachable_is_transient(exc):
    """The read timeout in production was one of these."""
    assert is_transient(exc) is True


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_retryable_statuses_are_transient(status):
    exc = httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://x.test"),
        response=httpx.Response(status),
    )
    assert is_transient(exc) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_ours(status):
    """A 401 means our credentials are wrong and a 404 means our URL is — both
    are things someone can actually fix, so they keep their traceback."""
    exc = httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://x.test"),
        response=httpx.Response(status),
    )
    assert is_transient(exc) is False


@pytest.mark.parametrize("exc", [ValueError("bad parse"), KeyError("id"), TypeError("x")])
def test_our_own_bugs_are_not_transient(exc):
    assert is_transient(exc) is False


def test_transient_logs_warning_without_a_traceback(caplog):
    """117 tracebacks for a flaky upstream is how a digest becomes unreadable."""
    logger = logging.getLogger("t.transient")
    with caplog.at_level(logging.DEBUG, logger="t.transient"):
        log_fetch_failure(logger, httpx.ReadTimeout("timed out"),
                          "Adzuna keyword '%s' failed", "Forward Deployed Engineer")
    rec = caplog.records[-1]
    assert rec.levelno == logging.WARNING
    assert rec.exc_info is None
    assert "Forward Deployed Engineer" in rec.getMessage()


def test_unexpected_keeps_error_and_the_traceback(caplog):
    logger = logging.getLogger("t.bug")
    with caplog.at_level(logging.DEBUG, logger="t.bug"):
        try:
            raise ValueError("we broke the parser")
        except ValueError as exc:
            log_fetch_failure(logger, exc, "Adzuna keyword '%s' failed", "x")
    rec = caplog.records[-1]
    assert rec.levelno == logging.ERROR
    assert rec.exc_info is not None      # the case where someone needs the stack


def test_message_formatting_survives_both_paths(caplog):
    logger = logging.getLogger("t.fmt")
    with caplog.at_level(logging.DEBUG, logger="t.fmt"):
        log_fetch_failure(logger, httpx.ConnectError("nope"), "%s board fetch crashed for '%s'",
                          "greenhouse", "Acme")
    assert "greenhouse board fetch crashed for 'Acme'" in caplog.records[-1].getMessage()
