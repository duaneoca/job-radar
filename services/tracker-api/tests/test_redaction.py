"""Credential redaction — the formatter chokepoint.

The 2026-09-03 incident: litellm surfaced Google's 429 as a raw httpx error
whose URL carried the user's key (`?key=AIzaSy…` — Google authenticates via
query param), and logger.exception printed the traceback into the pod logs and
the hourly digest email. The formatter is the one place that sees message text
AND tracebacks, whoever does the logging.
"""

import io
import logging

from app.logging_config import (
    DATE_FORMAT, LOG_FORMAT, _RedactingFormatter, configure_logging, redact,
)

# Same shape as a real Google key (AIza + 35 chars) but fabricated. NEVER put
# real key material in tests — the first version of this file copied the
# leaked key verbatim from the logs, and GitHub secret scanning rightly
# flagged the redaction PR for committing the very secret it redacts.
GOOGLE_KEY = "AIzaSyTESTTESTTESTTESTTESTTESTTESTTEST0"


def test_query_param_keys_are_redacted():
    url = f"https://g.example/v1/x:generateContent?key={GOOGLE_KEY}&alt=json"
    out = redact(url)
    assert GOOGLE_KEY not in out
    assert "?key=<redacted>&alt=json" in out


def test_bare_key_material_is_redacted_by_shape():
    for secret in (GOOGLE_KEY, "sk-ant-api03-abcdefghij0123456789", "gsk_abcdefghij0123456789"):
        out = redact(f"failed with credential {secret} somewhere")
        assert secret not in out, secret
        assert "<redacted>" in out


def test_adzuna_style_params_are_redacted():
    out = redact("GET https://api.adzuna.com/v1/api/jobs?app_id=12345&app_key=deadbeef")
    assert "12345" not in out and "deadbeef" not in out


def test_ordinary_text_is_untouched():
    line = "2026-09-03 23:39:39 ERROR app.llm: LLM completion failed (model=gemini/gemini-3.1-pro-preview)"
    assert redact(line) == line


def test_formatter_redacts_tracebacks():
    """The actual leak vector: the key was in the traceback, not the message."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    lg = logging.getLogger("redaction.test")
    lg.addHandler(handler)
    lg.propagate = False
    try:
        try:
            raise RuntimeError(f"429 for url 'https://g.example/x?key={GOOGLE_KEY}'")
        except RuntimeError:
            lg.exception("LLM completion failed (model=%s)", "gemini/x")
        out = stream.getvalue()
    finally:
        lg.removeHandler(handler)
    assert GOOGLE_KEY not in out
    assert "key=<redacted>" in out
    assert "LLM completion failed" in out


def test_digest_parser_still_matches_the_format():
    """The formatter must change nothing the digest regex depends on."""
    import re

    from app.log_digest import LOG_LINE

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    lg = logging.getLogger("redaction.format")
    lg.addHandler(handler)
    lg.propagate = False
    try:
        lg.error("something broke")
    finally:
        lg.removeHandler(handler)
    # The digest reads logs fetched with timestamps=true, so LOG_LINE expects
    # the kubelet's RFC3339 timestamp before our own.
    line = "2026-09-16T11:00:59.123456789Z " + stream.getvalue().splitlines()[0]
    assert re.match(LOG_LINE, line), line
