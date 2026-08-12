"""The memory probe must be incapable of changing a task's outcome.

It exists to diagnose a leak that only bites after ~2 days. Instrumentation that
can itself fail a review would be a far worse bug than the one it is hunting, so
most of what is tested here is that it stays out of the way.
"""

import logging

import pytest

from app import memlog


def test_rss_is_a_number_or_none():
    """None on non-Linux is a supported answer, not a failure."""
    v = memlog.rss_kb()
    assert v is None or (isinstance(v, int) and v > 0)


def test_measured_returns_the_wrapped_value():
    probe = memlog.MemoryProbe()

    @memlog.measured(lambda: probe)
    def work(a, b):
        return a + b

    assert work(2, 3) == 5
    assert probe.tasks == 1


def test_measured_still_records_when_the_task_raises():
    """The failure paths are the ones worth suspecting — a call that blew up
    still allocated. Measuring only successes would hide it."""
    probe = memlog.MemoryProbe()

    @memlog.measured(lambda: probe)
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()
    assert probe.tasks == 1


def test_a_broken_probe_cannot_fail_the_task(caplog):
    """The whole point. If the probe throws, the review must still succeed."""
    class Exploding:
        def record(self, *a, **k):
            raise RuntimeError("probe is broken")

    @memlog.measured(Exploding)
    def work():
        return "scored"

    with caplog.at_level(logging.WARNING):
        assert work() == "scored"
    assert any("memory probe failed" in r.message for r in caplog.records)


def test_a_broken_context_cannot_fail_the_task():
    """Context is built from task arguments, so a bad lambda is a real risk."""
    probe = memlog.MemoryProbe()

    @memlog.measured(lambda: probe, lambda *a, **k: 1 / 0)
    def work():
        return "scored"

    assert work() == "scored"


def test_context_is_logged_with_the_delta(caplog):
    probe = memlog.MemoryProbe()

    @memlog.measured(lambda: probe, lambda job: f"job={job}")
    def work(job):
        return None

    with caplog.at_level(logging.INFO, logger="app.memlog"):
        work("abc123")

    if memlog.rss_kb() is None:
        pytest.skip("no /proc/self/status — the delta line is what carries context")
    assert any("job=abc123" in r.getMessage() for r in caplog.records)


def test_profiling_is_off_by_default():
    """tracemalloc roughly doubles allocation cost. It must never be the
    default just because someone forgot a flag."""
    assert memlog.PROFILE_ENABLED is False or "MEMORY_PROFILE" in __import__("os").environ


def test_report_survives_being_called_without_a_prior_snapshot():
    """The first snapshot has nothing to compare against — it must record a
    baseline rather than blow up."""
    probe = memlog.MemoryProbe()
    probe._report()          # no prior snapshot
    probe._report()          # now compares against one
