"""Memory diagnostics for locating the ai-reviewer leak.

Why this exists — the measured facts, not a theory:

  * A fresh ForkPoolWorker child sits at ~160Mi RSS.
  * After ~42h of reviews the same child reaches the 512Mi cgroup limit and the
    kernel kills something: usually the child (Celery logs WorkerLostError and
    forks a replacement) and sometimes PID 1 (exit 137, `OOMKilled`, which the
    hourly digest can never see — SIGKILL leaves nothing to log).
  * `memory.events` recorded oom_kill=4 in one container's 42h.
  * Raising the ceiling has already been tried. On 2026-08-05 the limit went
    256→512Mi because idle sat at 213Mi; a week later idle sat at 510Mi. A leak
    just takes longer to reach a higher ceiling.
  * litellm's own client cache is NOT the cause: `in_memory_llm_clients_cache`
    is bounded (max_size_in_memory=200, ttl=600) and held 3 entries in
    production. Eliminated by inspection, so don't re-suspect it.

So the growth is somewhere else, and the honest way to find it is to measure
per task rather than reason about it.

Two levels, deliberately separated because one is free and one is not:

  `rss_kb()` reads /proc/self/status. Microseconds, allocates nothing, safe to
  run on every task. It answers "how much does one review cost us permanently,
  and does it correlate with a user, a model, or a prompt size?" — which is
  enough on its own to tell a steady per-task leak apart from one pathological
  input.

  tracemalloc attributes retained bytes to the source line that allocated them.
  It roughly doubles allocation cost and keeps its own bookkeeping, so it is
  opt-in via MEMORY_PROFILE=1 and only snapshots every Nth task. This is the
  level that actually names the leak.

Everything here logs at INFO. A leak under investigation is not yet an operator
error, and ERROR is reserved for what the hourly digest should mail about —
filling that digest with our own diagnostics would defeat its purpose.
"""

import functools
import gc
import logging
import os
import tracemalloc
from collections import Counter

logger = logging.getLogger(__name__)


def _flag(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().lower()


#: Deep profiling is off unless explicitly asked for. Reading it at import is
#: fine — the value is set on the pod, and a restart is how you change it.
PROFILE_ENABLED = _flag("MEMORY_PROFILE") in ("1", "true", "yes", "on")

#: Snapshot cadence. Every task would be needlessly expensive; the leak is
#: measured in hundreds of KB per task, so a window of ~25 makes the signal
#: clearly larger than the noise.
PROFILE_EVERY = max(1, int(os.getenv("MEMORY_PROFILE_EVERY", "25") or 25))

#: How many allocation sites to name per snapshot.
PROFILE_TOP = max(1, int(os.getenv("MEMORY_PROFILE_TOP", "8") or 8))

#: tracemalloc frame depth. 1 names the allocating line, which is usually inside
#: a library; more frames show who called it, which is what makes it actionable.
PROFILE_FRAMES = max(1, int(os.getenv("MEMORY_PROFILE_FRAMES", "5") or 5))


def rss_kb() -> int | None:
    """Resident set size of THIS process in KiB, or None where unavailable.

    /proc/self/status rather than resource.getrusage: ru_maxrss is a high-water
    mark that never comes back down, so it cannot show a task releasing memory
    and would make every task look like a leak.

    Returns None rather than raising on non-Linux (developer laptops, where this
    module still has to import for the tests to run).
    """
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


class MemoryProbe:
    """Per-task RSS accounting, and periodic allocation-site attribution.

    One instance per worker process. `record()` wraps a single task and returns
    the RSS delta in KiB so the caller can attach its own context (user, model,
    prompt size) to the same log line — correlation is the whole point, and a
    delta with no context can't be acted on.
    """

    def __init__(self) -> None:
        self.tasks = 0
        self.baseline_kb: int | None = rss_kb()
        self._snapshot = None
        self._census: Counter | None = None
        if PROFILE_ENABLED and not tracemalloc.is_tracing():
            tracemalloc.start(PROFILE_FRAMES)
            logger.info(
                "Memory profiling ON (every %d tasks, top %d, %d frames) — "
                "this costs allocation speed; unset MEMORY_PROFILE when done",
                PROFILE_EVERY, PROFILE_TOP, PROFILE_FRAMES,
            )

    def record(self, before_kb: int | None, context: str = "") -> int | None:
        """Log one task's RSS delta. Returns the delta in KiB, or None."""
        self.tasks += 1
        now_kb = rss_kb()
        delta = None
        if before_kb is not None and now_kb is not None:
            delta = now_kb - before_kb
            grown = now_kb - self.baseline_kb if self.baseline_kb is not None else None
            logger.info(
                "mem task=%d rss=%dMi delta=%+dKi since_fork=%s%s",
                self.tasks,
                now_kb // 1024,
                delta,
                f"{grown // 1024:+d}Mi" if grown is not None else "?",
                f" {context}" if context else "",
            )
        if PROFILE_ENABLED and self.tasks % PROFILE_EVERY == 0:
            self._report()
        return delta

    def _report(self) -> None:
        """Name the allocation sites that grew since the previous snapshot.

        Comparing against the PREVIOUS snapshot rather than a fixed baseline: a
        one-off cost at startup (imports, tokenizer tables) shows up once and
        would otherwise dominate every later report and hide the steady growth
        we're hunting.
        """
        snapshot = None
        if tracemalloc.is_tracing():
            try:
                snapshot = tracemalloc.take_snapshot()
            except Exception as exc:                 # pragma: no cover - defensive
                logger.warning("tracemalloc snapshot failed: %s", exc)

        if snapshot is not None and self._snapshot is not None:
            stats = snapshot.compare_to(self._snapshot, "traceback")[:PROFILE_TOP]
            logger.info("mem top growth over last %d tasks:", PROFILE_EVERY)
            for i, stat in enumerate(stats, 1):
                where = stat.traceback[0] if stat.traceback else "?"
                logger.info(
                    "  %d. %+.1fKi (%+d blocks) %s",
                    i, stat.size_diff / 1024, stat.count_diff, where,
                )
                # The allocating line is often inside a library; the caller is
                # what tells us which of OUR paths is responsible.
                for frame in list(stat.traceback)[1:PROFILE_FRAMES]:
                    logger.info("       from %s", frame)
        if snapshot is not None:
            self._snapshot = snapshot

        # A census of live objects by type. Cheaper to read than a traceback and
        # frequently the faster answer: "+25 httpx.Client" names the leak on its
        # own, where a byte count only says something grew.
        census = Counter(type(o).__name__ for o in gc.get_objects())
        if self._census is not None:
            growth = [
                (name, census[name] - self._census.get(name, 0))
                for name in census
                if census[name] - self._census.get(name, 0) > 0
            ]
            growth.sort(key=lambda kv: kv[1], reverse=True)
            if growth:
                logger.info(
                    "mem object growth: %s",
                    ", ".join(f"{n}+{d}" for n, d in growth[:PROFILE_TOP]),
                )
        self._census = census

        # Uncollectable cycles are a leak all of their own, and a silent one.
        if gc.garbage:
            logger.info("mem gc.garbage holds %d uncollectable objects", len(gc.garbage))


def measured(probe_factory, context=None):
    """Decorator: record the RSS delta of one task, on every exit path.

    A decorator rather than inline try/finally because the task has eight early
    returns (no key, no model, blocking error, truncation, permanent failure,
    retry exhaustion, unusable output, success). Measuring only the success path
    would hide exactly the cases worth suspecting — a failed call that still
    retains its response is a leak that looks like nothing happened.

    `probe_factory` is a callable so the probe is built inside the forked child
    rather than captured at decoration time in the parent. `context` is an
    optional callable over the task's own arguments — a bare number is not
    actionable, but one tagged with the job and user is.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            before = rss_kb()
            try:
                return fn(*args, **kwargs)
            finally:
                # Never let instrumentation change the task's outcome: a probe
                # that raises would turn a successful review into a retry.
                try:
                    label = context(*args, **kwargs) if context else ""
                    probe_factory().record(before, context=label)
                except Exception as exc:            # pragma: no cover - defensive
                    logger.warning("memory probe failed: %s", exc)
        return wrapper
    return decorate
