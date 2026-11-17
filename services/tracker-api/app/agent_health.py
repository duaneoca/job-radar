"""Is a user's email agent reaching Job Radar? Inferred from agent_runs alone.

The agent can't report that it can't reach us — when the network is down,
reporting fails too (283 of 298 measured failed local runs had no record at
all). So the server infers it from absence: a user whose agent normally runs
every N minutes and hasn't been heard from in several N is unreachable. Runs
spooled during the outage arrive later marked `late`; they backfill the
history, so the outage then shows as a period rather than a silence that looks
like "no mail".

Pure functions over timestamps, so the rules are testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

HEALTHY = "healthy"
UNREACHABLE = "unreachable"
NEVER_CONNECTED = "never_connected"

CADENCE_SAMPLE = 20                 # recent runs used to infer a local agent's cadence
MISSED_INTERVALS = 3                # unreachable after this many missed runs…
MIN_SILENCE = timedelta(hours=1)    # …but never sooner than this
HISTORY_WINDOW = timedelta(days=7)  # how far back periods are listed


@dataclass(frozen=True)
class Run:
    started_at: datetime
    finished_at: datetime | None
    late: bool
    environment: str          # "local" | "cloud"


@dataclass
class Period:
    kind: str                 # "late": runs happened but reached us late; "silent": no runs at all
    start: datetime
    end: datetime


@dataclass
class Health:
    state: str
    last_seen_at: datetime | None = None
    unreachable_since: datetime | None = None
    expected_interval_minutes: float | None = None
    environment: str | None = None
    periods: list[Period] = field(default_factory=list)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:          # SQLite in tests
        return dt.replace(tzinfo=timezone.utc)
    return dt


def expected_interval(runs: list[Run], cloud_interval: timedelta) -> timedelta | None:
    """How often this user's agent runs. Hosted (cloud) runs on the CronJob's
    schedule; a local agent's cadence is whatever the user set, so it's the
    median gap between their recent runs. None until there are two runs — so a
    user with a single run has no cadence yet and is judged by MIN_SILENCE
    alone: unreachable after an hour of silence. Acceptable; it corrects itself
    from the second run on."""
    if not runs:
        return None
    newest = max(runs, key=lambda r: r.started_at)
    if newest.environment == "cloud":
        return cloud_interval
    recent = sorted(r.started_at for r in runs)[-CADENCE_SAMPLE:]
    gaps = [b - a for a, b in zip(recent, recent[1:]) if b > a]
    return median(gaps) if gaps else None


def silence_threshold(interval: timedelta | None) -> timedelta:
    return max(interval * MISSED_INTERVALS, MIN_SILENCE) if interval else MIN_SILENCE


def compute(runs: list[Run], now: datetime, cloud_interval: timedelta) -> Health:
    runs = [Run(_aware(r.started_at), _aware(r.finished_at), r.late, r.environment) for r in runs]
    if not runs:
        return Health(state=NEVER_CONNECTED)

    runs.sort(key=lambda r: r.started_at)
    interval = expected_interval(runs, cloud_interval)
    threshold = silence_threshold(interval)
    last_seen = runs[-1].started_at

    unreachable = now - last_seen > threshold
    health = Health(
        state=UNREACHABLE if unreachable else HEALTHY,
        last_seen_at=last_seen,
        unreachable_since=last_seen if unreachable else None,
        expected_interval_minutes=round(interval.total_seconds() / 60, 1) if interval else None,
        environment=runs[-1].environment,
    )

    # Periods in the window: contiguous late runs, and gaps longer than the
    # threshold. A late record fills a gap, so a flushed outage turns from
    # "silent" into "late" — the history then says what actually happened.
    cutoff = now - HISTORY_WINDOW
    window = [r for r in runs if r.started_at >= cutoff]
    periods: list[Period] = []
    for prev, cur in zip(window, window[1:]):
        if cur.started_at - prev.started_at > threshold:
            periods.append(Period("silent", prev.started_at, cur.started_at))
    late_start = late_end = None
    for r in window + [None]:
        if r is not None and r.late:
            late_start = late_start or r.started_at
            late_end = r.finished_at or r.started_at
        elif late_start is not None:
            periods.append(Period("late", late_start, late_end))
            late_start = late_end = None
    if unreachable and last_seen >= cutoff:
        periods.append(Period("silent", last_seen, now))
    health.periods = sorted(periods, key=lambda p: p.start, reverse=True)
    return health
