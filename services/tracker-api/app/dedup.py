"""Spotting the same job listed again.

Aggregators re-list one opening many times: Adzuna re-posts an ad under a new
id, JSearch re-issued its own ids daily, and a posting shows up via several
boards. In production (2026-10) one user had 2,847 extra copies by company +
title — each scored by AI at their expense. A job's `match_key` is its company,
title and CITY with everything but letters and digits removed, so "Applied AI"
and "AppliedAI", or an en dash and a hyphen, read the same.

Same city on purpose: the same title in Boston and in Chicago can be two real
openings. And only AGGREGATOR listings are ever skipped as copies — company
boards (Greenhouse/Ashby/Lever) carry stable ids, where two identical titles are
usually two reqs, and a manual import is a deliberate act.

Migration 0030 backfills the column with the SQL twin of `match_key`:
    regexp_replace(lower(x), '[^a-z0-9]', '', 'g')  per part, city = text before
    the first comma. Keep the two in step (SQLite has no regexp_replace, so the
backfill was checked against real Postgres when written — see the PR).
"""

import re

AGGREGATOR_SOURCES = frozenset({"adzuna", "jsearch", "the_muse", "remotive"})

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _norm(text: str | None) -> str:
    return _NON_ALNUM.sub("", (text or "").lower())


def match_key(company: str | None, title: str | None, location: str | None) -> str | None:
    """`company|title|city`, normalised; None when company or title is blank —
    a key of empty parts would match every other blank listing."""
    c, t = _norm(company), _norm(title)
    if not c or not t:
        return None
    city = _norm((location or "").split(",")[0])
    return f"{c}|{t}|{city}"[:255]
