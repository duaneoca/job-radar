"""The hourly error digest.

Two things can quietly ruin this feature and neither shows up as a crash:
grouping that doesn't group (1,115 identical paragraphs), and a filter that
matches nothing (a permanently silent digest that looks like a healthy system).
Most of these tests are aimed at those.
"""

from collections import OrderedDict

import pytest

from app import log_digest
from app.log_digest import Entry, collect, normalise, render

# kubelet prefixes each line with its own RFC3339 timestamp (timestamps=true),
# then our own format follows. This is verbatim what a real worker emits —
# second-resolution, because logging_config sets an explicit datefmt. Writing
# these with milliseconds is how the parser first shipped matching nothing.
def line(level="ERROR", logger="app.jobs", message="boom", ts="04:29:02"):
    return f"2026-08-01T{ts}.042000000Z 2026-08-01 {ts} {level} {logger}: {message}"


def _collect(*lines, service="tracker-api"):
    entries: OrderedDict = OrderedDict()
    collect(service, "\n".join(lines), entries)
    return entries


# ── grouping ──────────────────────────────────────────────────

def test_same_fault_different_ids_is_one_entry():
    """The case this feature was built for: one bug, 1,115 jobs."""
    entries = _collect(
        line(message="Failed to post review for job 8bca15d1-3f2e-4a1b-9c8d-1122334455aa"),
        line(message="Failed to post review for job b30cc49b-1111-2222-3333-444455556666"),
    )
    assert len(entries) == 1
    assert next(iter(entries.values())).count == 2


@pytest.mark.parametrize("a,b", [
    ("scored 7.5 jobs", "scored 9.0 jobs"),
    ("user duane@x.org failed", "user someone@y.net failed"),
    ("fetch https://a.example/1 failed", "fetch https://b.example/22 failed"),
    ("model 'gemini-2.5' rejected", "model 'gpt-5.4' rejected"),
    ("token a1b2c3d4e5f60718 expired", "token ffffffffffffffff expired"),
])
def test_varying_parts_collapse(a, b):
    assert len(_collect(line(message=a), line(message=b))) == 1


def test_genuinely_different_messages_stay_separate():
    entries = _collect(line(message="database is gone"), line(message="disk is full"))
    assert len(entries) == 2


def test_same_message_from_different_services_stays_separate():
    """"Connection refused" in the scraper and in the API are different problems."""
    entries: OrderedDict = OrderedDict()
    collect("scraper", line(message="Connection refused"), entries)
    collect("tracker-api", line(message="Connection refused"), entries)
    assert len(entries) == 2


def test_same_message_from_different_loggers_stays_separate():
    entries = _collect(
        line(logger="app.jobs", message="boom"),
        line(logger="app.keys", message="boom"),
    )
    assert len(entries) == 2


def test_example_keeps_the_real_text():
    """The grouping key is normalised; what you read in the email is not."""
    entries = _collect(line(message="Failed for job 8bca15d1-3f2e-4a1b-9c8d-1122334455aa"))
    assert "8bca15d1" in next(iter(entries.values())).example


def test_normalise_is_stable_for_an_already_generic_message():
    assert normalise("database is gone") == "database is gone"


# ── filtering ─────────────────────────────────────────────────

def test_info_and_warning_are_not_reported():
    """WARNING is where user-fixable problems live. Mailing a retired model to
    the operator is the noise this filter exists to prevent."""
    entries = _collect(
        line(level="INFO", message="Posted review"),
        line(level="WARNING", message="Permanent LLM failure (invalid_model) for user x"),
        line(level="DEBUG", message="chatty"),
    )
    assert entries == {}


def test_critical_is_reported():
    assert len(_collect(line(level="CRITICAL", message="the wheels came off"))) == 1


def test_unparseable_lines_alone_are_ignored():
    """Third-party output that doesn't use our format can't be counted as a
    fault, or every library banner becomes an error."""
    assert _collect("some bare line with no level at all") == {}


# ── tracebacks ────────────────────────────────────────────────

def test_traceback_attaches_and_is_not_counted():
    entries = _collect(
        line(message="Unhandled"),
        "Traceback (most recent call last):",
        '  File "/app/app/jobs.py", line 12, in save',
        "ValueError: nope",
    )
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry.count == 1
    assert "ValueError: nope" in entry.traceback


def test_a_normal_line_ends_the_traceback():
    """Otherwise an INFO line's continuation would land on the previous error."""
    entries = _collect(
        line(message="Unhandled"),
        "ValueError: nope",
        line(level="INFO", message="carrying on"),
        "this belongs to nothing",
    )
    entry = next(iter(entries.values()))
    assert entry.traceback == ["ValueError: nope"]


def test_traceback_capture_is_bounded():
    """A runaway stack must not put a megabyte in an email."""
    entries = _collect(line(message="Unhandled"), *[f"  frame {i}" for i in range(200)])
    assert len(next(iter(entries.values())).traceback) <= 25


# ── rendering ─────────────────────────────────────────────────

def _entry(service, count, example="boom", level="ERROR", logger_name="app.jobs"):
    e = Entry(service, logger_name, level, example)
    e.count = count
    return e


def test_subject_counts_types_and_totals():
    entries = OrderedDict({
        ("a",): _entry("tracker-api", 187),
        ("b",): _entry("scraper", 27, example="scrape failed"),
    })
    subject, _ = render(entries, scanned=11, unreadable=[])
    assert "2 error types" in subject
    assert "214 errors" in subject


def test_subject_is_singular_for_one_error():
    subject, _ = render(OrderedDict({("a",): _entry("tracker-api", 1)}), 3, [])
    assert "1 error type," in subject and "1 error " in subject


def test_body_sorts_by_count_and_shows_the_multiplier():
    entries = OrderedDict({
        ("a",): _entry("tracker-api", 3, example="rare"),
        ("b",): _entry("tracker-api", 99, example="common"),
    })
    _, body = render(entries, 5, [])
    assert body.index("[99×]") < body.index("[3×]")


def test_body_names_unreadable_containers():
    """The digest must never overstate its own coverage."""
    _, body = render(OrderedDict({("a",): _entry("tracker-api", 1)}), 5, ["pod-x/api"])
    assert "pod-x/api" in body
    assert "Could NOT read 1" in body


def test_body_states_the_repeat_is_deliberate():
    _, body = render(OrderedDict({("a",): _entry("tracker-api", 1)}), 5, [])
    assert "deliberate" in body
    assert "rotate" in body      # honest about being best-effort


# ── main() ────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, logs):
        self.logs = logs


@pytest.fixture
def wired(monkeypatch):
    """Replace the whole Kubernetes layer; capture what would be mailed."""
    sent = []
    monkeypatch.setattr(log_digest, "send_email",
                        lambda to, subject, body: sent.append((to, subject, body)) or True)
    monkeypatch.setattr(log_digest, "_namespace", lambda: "jobradar-production")
    monkeypatch.setattr(log_digest, "_kube_client", lambda: _FakeClient({}))
    monkeypatch.setattr(log_digest.settings, "admin_notify_email", "ops@example.com")
    monkeypatch.setattr(log_digest, "find_incidents", lambda c, ns: OrderedDict())
    return sent


def test_quiet_hour_sends_nothing(monkeypatch, wired):
    monkeypatch.setattr(log_digest, "list_containers",
                        lambda c, ns: [("pod-1", "api", "tracker-api")])
    monkeypatch.setattr(log_digest, "read_log",
                        lambda c, ns, p, ct: line(level="INFO", message="all good"))
    assert log_digest.main() == 0
    assert wired == []


def test_errors_are_mailed(monkeypatch, wired):
    monkeypatch.setattr(log_digest, "list_containers",
                        lambda c, ns: [("pod-1", "api", "tracker-api")])
    monkeypatch.setattr(log_digest, "read_log", lambda c, ns, p, ct: line(message="boom"))
    assert log_digest.main() == 0
    assert len(wired) == 1
    to, subject, body = wired[0]
    assert to == "ops@example.com"
    assert "boom" in body


def test_one_unreadable_container_does_not_lose_the_others(monkeypatch, wired):
    def read(c, ns, pod, container):
        if pod == "pod-bad":
            raise RuntimeError("403")
        return line(message="boom")

    monkeypatch.setattr(log_digest, "list_containers", lambda c, ns: [
        ("pod-1", "api", "tracker-api"), ("pod-bad", "worker", "scraper"),
    ])
    monkeypatch.setattr(log_digest, "read_log", read)
    assert log_digest.main() == 0
    _, _, body = wired[0]
    assert "boom" in body
    assert "pod-bad/worker" in body


def test_unreadable_containers_alone_still_notify(monkeypatch, wired):
    """No errors found, but we couldn't look everywhere — silence would be a lie."""
    monkeypatch.setattr(log_digest, "list_containers",
                        lambda c, ns: [("pod-bad", "worker", "scraper")])
    monkeypatch.setattr(log_digest, "read_log",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("403")))
    assert log_digest.main() == 0
    assert "could not read" in wired[0][1].lower()


def test_total_failure_mails_and_exits_nonzero(monkeypatch, wired):
    """A digest that dies quietly leaves the operator believing all is well."""
    monkeypatch.setattr(log_digest, "list_containers",
                        lambda c, ns: (_ for _ in ()).throw(RuntimeError("connection refused")))
    assert log_digest.main() == 1
    _, subject, body = wired[0]
    assert "FAILED" in subject
    assert "connection refused" in body


def test_no_recipient_configured_is_a_no_op(monkeypatch, wired):
    monkeypatch.setattr(log_digest.settings, "admin_notify_email", "")
    assert log_digest.main() == 0
    assert wired == []


# ── services that don't use our log format ────────────────────
# postgres, redis and nginx never match LOG_LINE. Without a fallback their
# failures would be invisible and the digest would quietly imply the database
# is fine.


def test_postgres_style_error_is_caught():
    entries = _collect(
        '2026-08-01T04:29:02.042Z 2026-08-01 04:29:02.042 UTC [7] ERROR:  '
        'relation "jobs" does not exist',
        service="postgres",
    )
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry.level == "ERROR"
    assert "does not exist" in entry.example


def test_postgres_fatal_is_caught():
    entries = _collect(
        "2026-08-01T04:29:02.042Z 2026-08-01 04:29:02.042 UTC [7] FATAL:  the database system is shutting down",
        service="postgres",
    )
    assert next(iter(entries.values())).level == "FATAL"


def test_fallback_groups_repeats_too():
    entries = _collect(
        '2026-08-01T04:29:02Z ... [7] ERROR:  duplicate key value violates unique constraint "uq_a"',
        '2026-08-01T04:29:03Z ... [8] ERROR:  duplicate key value violates unique constraint "uq_b"',
        service="postgres",
    )
    assert len(entries) == 1
    assert next(iter(entries.values())).count == 2


def test_fallback_does_not_fire_inside_our_traceback():
    """A stack frame that happens to contain "ERROR:" is not a second fault."""
    entries = _collect(
        line(message="Unhandled"),
        '  File "/app/x.py", line 3, in f  # ERROR: not a real one',
        "ValueError: nope",
    )
    assert len(entries) == 1
    assert next(iter(entries.values())).count == 1


def test_fallback_ignores_ordinary_output():
    assert _collect("2026-08-01T04:29:02Z LOG:  database system is ready", service="postgres") == {}


def test_parses_the_format_configure_logging_actually_emits():
    """Pinned against logging_config, not against what the regex hopes for.
    The parser and the formatter drifting apart is silent: no crash, no error,
    just a digest that never reports anything."""
    import logging

    from app.logging_config import DATE_FORMAT, LOG_FORMAT

    record = logging.LogRecord("app.jobs", logging.ERROR, "x", 1, "boom", None, None)
    emitted = logging.Formatter(LOG_FORMAT, DATE_FORMAT).format(record)
    entries = _collect(f"2026-08-01T04:29:02.042000000Z {emitted}")
    assert len(entries) == 1
    assert next(iter(entries.values())).example == "boom"


def test_milliseconds_are_still_accepted():
    """Tolerated so a datefmt change doesn't silence the digest."""
    assert len(_collect(
        "2026-08-01T04:29:02.042Z 2026-08-01 04:29:02,042 ERROR app.jobs: boom"
    )) == 1


# ── formats the digest must not miss ──────────────────────────

def test_uvicorns_own_error_format_is_caught():
    """uvicorn logs unhandled ASGI exceptions through its private `uvicorn.error`
    logger, which has propagate=False and keeps uvicorn's format regardless of
    what configure_logging does. Those are real 500s — the fallback is the only
    thing that sees them."""
    entries = _collect(
        "2026-08-01T04:53:17.000Z ERROR:    Exception in ASGI application",
        service="tracker-api",
    )
    assert len(entries) == 1
    assert "ASGI application" in next(iter(entries.values())).example


def test_celery_own_format_is_caught():
    """Celery's task-failure banner uses its own format even with
    worker_hijack_root_logger=False, since it's logged before our handler in the
    child process for some failure modes."""
    entries = _collect(
        "2026-08-01T04:50:35.143Z [2026-08-01 04:50:35,143: ERROR/ForkPoolWorker-1] "
        "Task app.tasks.review_job raised unexpected: ValueError('nope')",
        service="ai-reviewer",
    )
    assert len(entries) == 1


# ── failed runs and killed containers ─────────────────────────
# Invisible to the log scan: a CronJob run dying with a plain Python traceback,
# and a container the kernel OOM-kills (which logs nothing at all).

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 10, 13, 0, 5, tzinfo=timezone.utc)


def _ago(minutes):
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


AGENT_TRACEBACK = """2026-10-13T00:00:30Z Traceback (most recent call last):
2026-10-13T00:00:30Z   File "/app/run.py", line 9, in <module>
2026-10-13T00:00:30Z httpcore.ConnectError: [Errno 111] Connection refused
2026-10-13T00:00:30Z
2026-10-13T00:00:30Z The above exception was the direct cause of the following exception:
2026-10-13T00:00:31Z httpx.ConnectError: [Errno 111] Connection refused
"""


class _Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class _Kube:
    def __init__(self, pods, jobs, jobs_status=200):
        self.pods, self.jobs, self.jobs_status = pods, jobs, jobs_status

    def get(self, path, params=None):
        if path.endswith("/pods"):
            return _Resp({"items": self.pods})
        if path.endswith("/jobs"):
            return _Resp({"items": self.jobs}, self.jobs_status)
        raise AssertionError(path)


def _job(name, minutes_ago, reason="BackoffLimitExceeded", cron="jobradar-email-agent"):
    return {"metadata": {"name": name, "ownerReferences": [{"kind": "CronJob", "name": cron}]},
            "status": {"conditions": [{"type": "Failed", "status": "True", "reason": reason,
                                       "message": "Job has reached the specified backoff limit",
                                       "lastTransitionTime": _ago(minutes_ago)}]}}


def _job_pod(job):
    return {"metadata": {"name": f"{job}-abc", "labels": {"job-name": job},
                         "creationTimestamp": _ago(6)},
            "spec": {"containers": [{"name": "agent"}]}, "status": {}}


def _oom_pod(minutes_ago, slot="lastState"):
    return {"metadata": {"name": "jobradar-tracker-api-x", "labels": {"app": "tracker-api"}},
            "spec": {"containers": [{"name": "tracker-api"}]},
            "status": {"containerStatuses": [{"name": "tracker-api", slot: {"terminated": {
                "reason": "OOMKilled", "exitCode": 137, "finishedAt": _ago(minutes_ago)}}}]}}


def test_last_exception_names_what_the_run_died_of():
    assert log_digest.last_exception(AGENT_TRACEBACK) == "httpx.ConnectError: [Errno 111] Connection refused"
    assert log_digest.last_exception("just some output\n") is None


def test_failed_cronjob_runs_are_found_and_grouped(monkeypatch):
    jobs = [_job("agent-1", 5), _job("agent-2", 50), _job("agent-old", 120)]
    pods = [_job_pod("agent-1"), _job_pod("agent-2")]
    monkeypatch.setattr(log_digest, "read_log", lambda c, ns, p, ct: AGENT_TRACEBACK)
    inc = log_digest.find_incidents(_Kube(pods, jobs), "ns", now=NOW)
    (i,) = inc.values()                                   # two runs, one shape; the old one ignored
    assert (i.kind, i.source, i.reason, i.count) == ("failed run", "jobradar-email-agent",
                                                      "BackoffLimitExceeded", 2)
    assert "Connection refused" in i.detail


def test_a_failed_run_without_a_readable_log_still_reports():
    inc = log_digest.find_incidents(_Kube([], [_job("backup-1", 10, cron="jobradar-db-backup")]),
                                    "ns", now=NOW)
    (i,) = inc.values()
    assert i.source == "jobradar-db-backup" and "backoff limit" in i.detail


@pytest.mark.parametrize("slot", ["lastState", "state"])
def test_oom_kills_are_reported(slot):
    (i,) = log_digest.find_incidents(_Kube([_oom_pod(20, slot)], []), "ns", now=NOW).values()
    assert (i.kind, i.source, i.reason) == ("killed", "tracker-api", "OOMKilled")


def test_old_kills_are_not_reported():
    assert not log_digest.find_incidents(_Kube([_oom_pod(70 + 60)], []), "ns", now=NOW)


def test_failed_runs_alone_are_mailed(monkeypatch, wired):
    inc = OrderedDict()
    log_digest._add(inc, "failed run", "jobradar-email-agent", "BackoffLimitExceeded",
                    "httpx.ConnectError: [Errno 111] Connection refused")
    monkeypatch.setattr(log_digest, "find_incidents", lambda c, ns: inc)
    monkeypatch.setattr(log_digest, "list_containers", lambda c, ns: [("pod-1", "api", "tracker-api")])
    monkeypatch.setattr(log_digest, "read_log", lambda c, ns, p, ct: line(level="INFO", message="ok"))
    assert log_digest.main() == 0
    _, subject, body = wired[0]
    assert "1 failed run" in subject
    assert "jobradar-email-agent" in body and "Connection refused" in body


def test_a_refused_jobs_check_is_named_not_silent(monkeypatch, wired):
    """Most likely the batch/jobs permission is missing; the log scan still runs."""
    monkeypatch.setattr(log_digest, "find_incidents",
                        lambda c, ns: log_digest.find_incidents(_Kube([], [], jobs_status=403), ns))
    monkeypatch.setattr(log_digest, "list_containers", lambda c, ns: [("pod-1", "api", "tracker-api")])
    monkeypatch.setattr(log_digest, "read_log", lambda c, ns, p, ct: line(level="INFO", message="ok"))
    assert log_digest.main() == 0
    assert "failed-run check" in wired[0][2]
