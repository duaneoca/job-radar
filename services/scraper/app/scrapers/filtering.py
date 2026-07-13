"""Shared keyword-filtering and text helpers for stage-1 source filtering.

Extracted from remotive.py so company-board sources (ats_boards.py) reuse the
same tokenization. Stage-1 filters are intentionally broad — stage-2 AI review
does the real selection — but they are mandatory for sources that return a
company's ENTIRE board (a single Ashby board can be 100+ roles), to keep the
scoring budget bounded.
"""

import re as _re
from typing import List

# Words that are too generic to be useful as a standalone match.
STOPWORDS = {"senior", "junior", "staff", "lead", "principal", "engineer", "developer"}


def keyword_tokens(keywords: List[str]) -> set[str]:
    tokens: set[str] = set()
    for kw in keywords or []:
        for tok in kw.lower().split():
            if len(tok) >= 3 and tok not in STOPWORDS:
                tokens.add(tok)
    return tokens


def title_matches(title: str, tokens: set[str]) -> bool:
    """True if the job title contains ANY keyword token (title-only variant,
    for sources without tags)."""
    low = (title or "").lower()
    return any(tok in low for tok in tokens)


def matches_any_token(item: dict, tokens: set[str]) -> bool:
    """Remotive-style match: title or tags contain any token."""
    if title_matches(item.get("title") or "", tokens):
        return True
    for tag in item.get("tags") or []:
        low = tag.lower()
        if any(tok in low for tok in tokens):
            return True
    return False


_HTML_TAG_RE = _re.compile(r"<[^>]+>")
_WHITESPACE_RE = _re.compile(r"\s+")


def strip_html(text: str) -> str:
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", text)).strip()
