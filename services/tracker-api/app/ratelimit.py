"""Tiny in-process rate limiter for the login endpoint.

Sliding-window, keyed by client IP, to throttle password brute-force /
credential-stuffing. Deliberately keyed on the *caller's IP* (not the target
email) so an attacker can't lock a victim out of their own account by spamming
failed logins for that email.

In-memory is sufficient here: prod tracker-api runs a single replica, so there's
no cross-process state to share. Counters reset on restart (acceptable — a pod
restart is not attacker-triggerable) and are self-pruning.
"""
from __future__ import annotations

import threading
import time

# Allow a modest burst of failures, then hard-throttle for the rest of the window.
_WINDOW_SEC = 15 * 60
_MAX_FAILURES = 10
# The table is keyed by caller IP, and a caller who can vary the key (a forged
# header, or many addresses) could grow it without bound — on a pod that has
# fought OOMs. Entries are otherwise pruned only when their own key is touched
# again, so cap the total and sweep when it is reached.
_MAX_KEYS = 10_000

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _fresh(ts: list[float], now: float) -> list[float]:
    return [t for t in ts if now - t < _WINDOW_SEC]


def is_allowed(key: str, limit: int = _MAX_FAILURES) -> bool:
    """False once the key has hit `limit` events within the window."""
    now = time.time()
    with _lock:
        ts = _fresh(_failures.get(key, []), now)
        if ts:
            _failures[key] = ts
        else:
            _failures.pop(key, None)
        return len(ts) < limit


def _cap(now: float) -> None:
    """Under the lock. Sweep stale keys once the table is full; if it is still
    full, drop the half that was touched longest ago."""
    if len(_failures) <= _MAX_KEYS:
        return
    for k in list(_failures):
        fresh = _fresh(_failures[k], now)
        if fresh:
            _failures[k] = fresh
        else:
            del _failures[k]
    if len(_failures) > _MAX_KEYS:
        for k in sorted(_failures, key=lambda k: _failures[k][-1])[: len(_failures) // 2]:
            del _failures[k]


def record_failure(key: str) -> None:
    now = time.time()
    with _lock:
        ts = _fresh(_failures.get(key, []), now)
        ts.append(now)
        _failures[key] = ts
        _cap(now)


def reset(key: str) -> None:
    """Clear a key's failures — called on a successful password check."""
    with _lock:
        _failures.pop(key, None)


def client_ip(headers, client) -> str:
    """The caller's IP for rate-limit keys, read only from headers the caller
    cannot set: CF-Connecting-IP is written by Cloudflare, X-Real-IP is
    overwritten by our nginx with its peer. Never X-Forwarded-For — nginx
    *appends* to whatever the client sent, so its first element is the
    client's to choose, and a chosen key is a bypassed limiter (and, sprayed,
    a memory-growth primitive). A caller who reaches the origin without
    going through Cloudflare could still forge CF-Connecting-IP; closing that
    is a firewall rule (allow 80/443 from Cloudflare's ranges only), which is
    why the limits that matter also have a global ceiling."""
    return (
        headers.get("cf-connecting-ip")
        or headers.get("x-real-ip")
        or (client.host if client else "unknown")
    )
