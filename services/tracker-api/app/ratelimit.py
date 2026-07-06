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

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _fresh(ts: list[float], now: float) -> list[float]:
    return [t for t in ts if now - t < _WINDOW_SEC]


def is_allowed(key: str) -> bool:
    """False once the key has hit the failure ceiling within the window."""
    now = time.time()
    with _lock:
        ts = _fresh(_failures.get(key, []), now)
        if ts:
            _failures[key] = ts
        else:
            _failures.pop(key, None)
        return len(ts) < _MAX_FAILURES


def record_failure(key: str) -> None:
    now = time.time()
    with _lock:
        ts = _fresh(_failures.get(key, []), now)
        ts.append(now)
        _failures[key] = ts


def reset(key: str) -> None:
    """Clear a key's failures — called on a successful password check."""
    with _lock:
        _failures.pop(key, None)


def client_ip(headers, client) -> str:
    """Best-effort real client IP behind Cloudflare + nginx.
    CF-Connecting-IP is set by Cloudflare to the true client; X-Real-IP is set by
    our nginx. Fall back to the socket peer."""
    return (
        headers.get("cf-connecting-ip")
        or headers.get("x-real-ip")
        or (headers.get("x-forwarded-for", "").split(",")[0].strip() or None)
        or (client.host if client else "unknown")
    )
