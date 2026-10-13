"""Per-route memory accounting for tracker-api.

Production tracker-api grows ~40Mi/day until its 512Mi limit OOM-kills it, and
a harness run can't say which of its routes does that — real traffic can. This
records, for every request, how the process RSS moved across it, totalled per
route TEMPLATE ("POST /jobs/{job_id}/ai-review", not each id), and every
MEMORY_REPORT_EVERY requests logs one INFO line naming the routes that grew it.

Read the report as a pointer, not proof: requests overlap (sync endpoints run on
a threadpool), so a delta can belong to a neighbour. Over hundreds of requests
the route that really ratchets the floor still rises to the top.

Always on — two small /proc reads per request. MEMORY_PROFILE=1 adds tracemalloc
attribution to each report (roughly doubles allocation cost: a diagnostic
session, not a running state; and note a deploy's `kubectl apply -k` resets
an env var set by hand). Everything here logs at INFO so it never reaches the
ERROR digest. ai-reviewer has its own memlog.py; the services share no package.
"""

import logging
import os
import tracemalloc
from collections import defaultdict

logger = logging.getLogger(__name__)

REPORT_EVERY = max(1, int(os.getenv("MEMORY_REPORT_EVERY", "500") or 500))
PROFILE_ENABLED = os.getenv("MEMORY_PROFILE", "").strip().lower() in ("1", "true", "yes", "on")
TOP_ROUTES = 6
TOP_SITES = 8


def rss_kb() -> int | None:
    """This process's resident set in KiB, or None off Linux."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


class RouteMemory:
    """Running RSS accounting by route, reported every REPORT_EVERY requests."""

    def __init__(self, report_every: int = REPORT_EVERY) -> None:
        self.report_every = report_every
        self.start_kb = rss_kb()
        self.requests = 0
        # route → [requests, summed delta KiB, largest single delta KiB]
        self.window: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        self._snapshot = None
        if PROFILE_ENABLED and not tracemalloc.is_tracing():
            tracemalloc.start(10)
            self._snapshot = tracemalloc.take_snapshot()

    def record(self, route: str, delta_kb: int) -> None:
        w = self.window[route]
        w[0] += 1
        w[1] += delta_kb
        w[2] = max(w[2], delta_kb)
        self.requests += 1
        if self.requests % self.report_every == 0:
            self.report()

    def report(self) -> str:
        now = rss_kb()
        top = sorted(self.window.items(), key=lambda kv: kv[1][1], reverse=True)[:TOP_ROUTES]
        parts = [f"{route} {s / 1024:+.1f}Mi/{n}req max{m / 1024:+.1f}Mi" for route, (n, s, m) in top]
        line = (f"mem rss={(now or 0) // 1024}Mi "
                f"since_start={((now or 0) - (self.start_kb or 0)) // 1024:+d}Mi "
                f"requests={self.requests} top: " + "; ".join(parts))
        logger.info(line)
        if self._snapshot is not None:
            snap = tracemalloc.take_snapshot()
            for stat in snap.compare_to(self._snapshot, "traceback")[:TOP_SITES]:
                logger.info("mem   %+.1fKi (%+d blocks) %s", stat.size_diff / 1024,
                            stat.count_diff, stat.traceback.format()[-1].strip())
            self._snapshot = snap
        self.window.clear()
        return line


_ACCOUNT: RouteMemory | None = None


def _account() -> RouteMemory:
    global _ACCOUNT
    if _ACCOUNT is None:
        _ACCOUNT = RouteMemory()
    return _ACCOUNT


class MemoryMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so it adds no task or buffering."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        before = rss_kb()
        try:
            await self.app(scope, receive, send)
        finally:
            after = rss_kb()
            if before is not None and after is not None:
                route = getattr(scope.get("route"), "path", None) or "(unmatched)"
                try:
                    _account().record(f"{scope.get('method', '?')} {route}", after - before)
                except Exception:          # accounting must never break a request
                    logger.debug("memlog record failed", exc_info=True)
