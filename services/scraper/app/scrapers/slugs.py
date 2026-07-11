"""Company-name → ATS board-slug resolution with a redis-backed cache.

ATS board APIs are keyed by a company slug (jobs.ashbyhq.com/ramp → "ramp")
that the user's free-text `target_companies` entries don't carry. We derive
candidate slugs from the name and probe the board endpoint; the ATS scraper
treats a 200 (valid board, even with zero postings) as resolution.

Cache semantics (redis is already present as the Celery broker):
  hit  → slug string, TTL 7 days
  miss → "" sentinel, TTL 24 hours (a newly created board is found within a day)
  429/transport errors during probing are TRANSIENT — never cached as misses.
Redis being down must never kill a scrape: every call falls back to an
in-process dict for the lifetime of the worker process.
"""

import logging
import re

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_HIT_TTL = 7 * 24 * 3600
_MISS_TTL = 24 * 3600
_MISS = ""  # cached-miss sentinel

# Corporate suffixes dropped for the short slug variant.
_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "company", "labs", "ltd", "pbc"}


def candidate_slugs(company: str) -> list[str]:
    """Ordered, deduped slug candidates for a display name.

    "Acme Corp." → ["acmecorp", "acme-corp", "acme"]
    """
    words = re.sub(r"[^a-z0-9\s-]", "", (company or "").lower()).split()
    if not words:
        return []
    candidates = ["".join(words), "-".join(words)]
    core = [w for w in words if w not in _SUFFIXES]
    if core and core != words:
        candidates += ["".join(core), "-".join(core)]
    if len(core) > 1:
        candidates.append(core[0])
    elif len(words) > 1:
        candidates.append(words[0])
    out: list[str] = []
    for c in candidates:
        if c and c not in out:
            out.append(c)
    return out


def _norm(company: str) -> str:
    return re.sub(r"\s+", "-", (company or "").strip().lower())


class SlugCache:
    """Tri-state cache: resolved slug / cached miss / unknown (None)."""

    def __init__(self, url: str | None = None):
        self._local: dict[str, str] = {}
        try:
            self._redis = redis.Redis.from_url(url or settings.redis_url,
                                               socket_timeout=2, decode_responses=True)
        except Exception:  # pragma: no cover — from_url itself rarely raises
            self._redis = None

    @staticmethod
    def _key(ats: str, company: str) -> str:
        return f"ats:slug:{ats}:{_norm(company)}"

    def get(self, ats: str, company: str) -> str | None:
        """Resolved slug, "" for a cached miss, or None when unknown."""
        key = self._key(ats, company)
        if self._redis is not None:
            try:
                val = self._redis.get(key)
                if val is not None:
                    return val
            except Exception:
                logger.warning("slug cache read failed (redis); using local fallback")
        return self._local.get(key)

    def set(self, ats: str, company: str, slug: str | None) -> None:
        """Cache a resolution (slug) or a miss (None → sentinel)."""
        key = self._key(ats, company)
        val = slug if slug else _MISS
        ttl = _HIT_TTL if slug else _MISS_TTL
        self._local[key] = val
        if self._redis is not None:
            try:
                self._redis.setex(key, ttl, val)
            except Exception:
                logger.warning("slug cache write failed (redis); kept local only")
