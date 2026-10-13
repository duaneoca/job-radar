"""Hourly error digest — read every Job Radar pod's logs, group, and mail it.

Run as a CronJob on the tracker-api image (`python -m app.log_digest`), so there
is no extra image and no extra dependency: httpx talks to the Kubernetes API and
boto3 sends the mail, and both are already installed here.

Design decisions worth not re-litigating:

* **ERROR and CRITICAL only.** WARNING is where user-fixable problems live — a
  retired model, a rejected key — and those are shown to the *user*, not the
  operator. Mailing them would bury the lines that need a human. This filter is
  only as good as that discipline: if a user-fixable condition starts logging at
  ERROR, this digest degrades into noise.

* **No cooldown, no suppression, no state.** A persistent error mails every hour
  on purpose. The fix is to fix the error, or to fix what's being logged. State
  would mean a database or a volume, and a digest that can silently stop is worse
  than one that repeats.

* **Nothing found → nothing sent.** A quiet hour is silence, not an empty email.

* **A failure to read logs is itself mailed.** A digest that dies quietly is the
  exact failure this feature exists to prevent.

Stateless, read-only, and it never touches the database.
"""

import logging
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings
from app.email import send_email
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
KUBE_API = "https://kubernetes.default.svc"

# 65 minutes against an hourly schedule. The 5-minute overlap can repeat a line
# that lands on the boundary; a gap would lose one silently, which is worse.
WINDOW_SECONDS = 3900
# Per container. Truncation is reported in the footer rather than hidden.
LOG_BYTE_LIMIT = 5 * 1024 * 1024

LEVELS = ("ERROR", "CRITICAL")

# Matches logging_config.LOG_FORMAT after kubelet's RFC3339 timestamp prefix
# (added by timestamps=true):
#   2026-08-01T04:29:02.042Z 2026-08-01 04:29:02,042 ERROR app.jobs: boom
LOG_LINE = re.compile(
    r"^(?P<k8s_ts>\S+)\s+"
    # Milliseconds are optional: logging_config sets an explicit datefmt, which
    # makes asctime second-resolution. Kept tolerant so a future datefmt change
    # can't silently stop every line from matching.
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<logger>[\w.\-]+):\s*"
    r"(?P<message>.*)$"
)

# Services we don't write — postgres, redis, nginx — never match the format
# above, so without this their failures would be invisible and the digest would
# quietly imply the database is fine. Postgres emits `... UTC [7] ERROR:  msg`
# and `FATAL:`/`PANIC:` the same way; redis and nginx are close enough.
# Only consulted for lines LOG_LINE rejected, and never while following one of
# our own tracebacks (a stack frame mentioning "ERROR:" is not a new fault).
FALLBACK_LINE = re.compile(r"\b(?P<level>ERROR|FATAL|PANIC|CRITICAL|EMERG|ALERT):\s+(?P<message>.+)$")

# Celery's own format, which puts the colon BEFORE the level:
#   [2026-08-01 04:50:35,143: ERROR/ForkPoolWorker-1] Task … raised unexpected
# `worker_hijack_root_logger = False` should mean this never appears — but a pod
# predating that change, or a rolled-back image, would otherwise be silently
# unmonitored, and a monitor that depends on its own fix having shipped is not
# much of a monitor.
CELERY_LINE = re.compile(r":\s*(?P<level>ERROR|CRITICAL)/[^\]]*\]\s*(?P<message>.+)$")

# Applied in order. Broadest identifiers first, so a UUID isn't half-eaten by the
# hex rule and a timestamp isn't shredded by the number rule.
_NORMALISE = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"), "<ts>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalise(message: str) -> str:
    """Collapse the varying parts of a message so repeats of the same fault group.

    `Failed to post review for job 8bca15d1-…` and the same line for a different
    job become one entry with a count, which is the difference between a readable
    email and 1,115 identical paragraphs.
    """
    out = message
    for pattern, repl in _NORMALISE:
        out = pattern.sub(repl, out)
    return out.strip()


class Entry:
    """One distinct fault, and how many times it happened."""

    def __init__(self, service: str, logger_name: str, level: str, example: str):
        self.service = service
        self.logger = logger_name
        self.level = level
        self.example = example
        self.count = 0
        self.traceback: list[str] = []


def collect(service: str, log_text: str, entries: "OrderedDict[tuple, Entry]") -> None:
    """Fold one container's log into `entries`, keyed by (service, logger, shape).

    Continuation lines — tracebacks, multi-line provider errors — don't match the
    log format, so they attach to the entry above them rather than being counted
    as faults of their own.
    """
    current: Entry | None = None
    for raw in log_text.splitlines():
        m = LOG_LINE.match(raw)
        if m is None:
            if current is not None:
                # Continuation of the error we're already following — a traceback
                # frame or a multi-line provider error.
                if raw.strip() and len(current.traceback) < 25:
                    current.traceback.append(raw.rstrip())
                continue
            fallback = CELERY_LINE.search(raw) or FALLBACK_LINE.search(raw)
            if fallback is None:
                continue
            message = fallback.group("message")
            key = (service, "(raw)", normalise(message))
            entry = entries.get(key)
            if entry is None:
                entry = Entry(service, "(raw)", fallback.group("level"), message)
                entries[key] = entry
            entry.count += 1
            # Deliberately not set as `current`: these services don't emit Python
            # tracebacks, and following them would swallow the next real line.
            continue

        if m.group("level") not in LEVELS:
            current = None      # a non-error line ends any traceback we were following
            continue

        message = m.group("message")
        key = (service, m.group("logger"), normalise(message))
        entry = entries.get(key)
        if entry is None:
            entry = Entry(service, m.group("logger"), m.group("level"), message)
            entries[key] = entry
        entry.count += 1
        current = entry


# ── Kubernetes API ────────────────────────────────────────────────────────────

def _kube_client() -> httpx.Client:
    token = (SA_DIR / "token").read_text().strip()
    return httpx.Client(
        base_url=KUBE_API,
        headers={"Authorization": f"Bearer {token}"},
        verify=str(SA_DIR / "ca.crt"),
        timeout=30,
    )


def _namespace() -> str:
    return os.environ.get("POD_NAMESPACE") or (SA_DIR / "namespace").read_text().strip()


def list_containers(client: httpx.Client, namespace: str) -> list[tuple[str, str, str]]:
    """(pod, container, service-label) for every pod in the namespace.

    No label selector on purpose. `project=jobradar` is set on the Deployment
    objects but NOT on their pod templates, so selecting on it matches zero pods
    and produces a permanently silent digest that looks like a healthy system.
    The namespace is already the boundary — everything in it is ours — and this
    way a new service is covered the day it ships rather than the day someone
    remembers to label it.
    """
    resp = client.get(f"/api/v1/namespaces/{namespace}/pods")
    resp.raise_for_status()
    out = []
    for pod in resp.json().get("items", []):
        meta = pod.get("metadata", {})
        name = meta.get("name", "?")
        service = meta.get("labels", {}).get("app", name)
        for c in pod.get("spec", {}).get("containers", []):
            out.append((name, c["name"], service))
    return out


def read_log(client: httpx.Client, namespace: str, pod: str, container: str) -> str:
    resp = client.get(
        f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
        params={
            "container": container,
            "sinceSeconds": WINDOW_SECONDS,
            "timestamps": "true",
            "limitBytes": LOG_BYTE_LIMIT,
        },
    )
    resp.raise_for_status()
    return resp.text


# ── Failed runs and killed containers ─────────────────────────────────────────
# Two kinds of failure the log scan can't see. A CronJob run that dies with a
# plain Python traceback matches neither LOG_LINE nor FALLBACK_LINE — three
# email-agent runs failed in one hour on 2026-10-12 and no digest said so. And a
# container the kernel OOM-kills logs nothing at all, so the log scan reads it as
# a quiet hour; tracker-api was OOM-killed in production that way. Both are in
# the API's object status, not in any log.

# Termination reasons worth mail. "Completed" is a normal exit; "Error" is a
# crash, and catches a SIGKILL that the cgroup accounting didn't label OOM.
KILL_REASONS = ("OOMKilled", "Error")

# The final line of a Python traceback: `httpx.ConnectError: [Errno 111] …`.
EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_][\w]*\.)*[A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt)\b.*$")
# `timestamps=true` prefixes every log line with an RFC3339 stamp.
_K8S_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+Z ")


class Incident:
    """A failed run or a killed container, grouped like Entry: same source, same
    reason, same shape of detail → one line with a count."""

    def __init__(self, kind: str, source: str, reason: str, detail: str):
        self.kind = kind
        self.source = source
        self.reason = reason
        self.detail = detail
        self.count = 0


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _recent(value: str | None, now: datetime) -> bool:
    t = _parse_time(value)
    return t is not None and 0 <= (now - t).total_seconds() <= WINDOW_SECONDS


def last_exception(log_text: str) -> str | None:
    """The last exception line in a log — what a failed run actually died of."""
    for raw in reversed(log_text.splitlines()):
        line = _K8S_STAMP.sub("", raw).strip()
        if EXCEPTION_LINE.match(line):
            return line[:300]
    return None


def _add(incidents: "OrderedDict[tuple, Incident]", kind, source, reason, detail) -> None:
    key = (kind, source, reason, normalise(detail))
    inc = incidents.get(key)
    if inc is None:
        inc = incidents[key] = Incident(kind, source, reason, detail)
    inc.count += 1


def find_incidents(client: httpx.Client, namespace: str,
                   now: datetime | None = None) -> "OrderedDict[tuple, Incident]":
    """Failed Jobs and killed containers in the window, grouped.

    Raises on an unreadable API (the caller names that in the footer). Reading
    Jobs needs `batch/jobs: list` — see log-digest-rbac.yaml.
    """
    now = now or datetime.now(timezone.utc)
    incidents: "OrderedDict[tuple, Incident]" = OrderedDict()

    resp = client.get(f"/api/v1/namespaces/{namespace}/pods")
    resp.raise_for_status()
    pods = resp.json().get("items", [])

    # Killed containers: the evidence lives in lastState (after a restart) or
    # state (a pod that will not be restarted, e.g. a Job's).
    for pod in pods:
        meta = pod.get("metadata", {})
        service = meta.get("labels", {}).get("app", meta.get("name", "?"))
        for cs in pod.get("status", {}).get("containerStatuses", []) or []:
            for slot in ("lastState", "state"):
                term = (cs.get(slot) or {}).get("terminated") or {}
                if term.get("reason") == "OOMKilled" and _recent(term.get("finishedAt"), now):
                    _add(incidents, "killed", service, "OOMKilled",
                         f"container {cs.get('name')} ran out of memory (exit {term.get('exitCode')})")
                elif (slot == "lastState" and term.get("reason") in KILL_REASONS
                      and _recent(term.get("finishedAt"), now)):
                    _add(incidents, "killed", service, term.get("reason", "?"),
                         f"container {cs.get('name')} restarted after exit {term.get('exitCode')}")

    # Failed Jobs — CronJob runs and one-offs alike.
    resp = client.get(f"/apis/batch/v1/namespaces/{namespace}/jobs")
    resp.raise_for_status()
    pods_by_job: dict[str, list[dict]] = {}
    for pod in pods:
        job = pod.get("metadata", {}).get("labels", {}).get("job-name")
        if job:
            pods_by_job.setdefault(job, []).append(pod)
    for job in resp.json().get("items", []):
        meta = job.get("metadata", {})
        failed = next((c for c in job.get("status", {}).get("conditions", []) or []
                       if c.get("type") == "Failed" and c.get("status") == "True"), None)
        if failed is None or not _recent(failed.get("lastTransitionTime"), now):
            continue
        owner = next((o.get("name") for o in meta.get("ownerReferences", []) or []
                      if o.get("kind") == "CronJob"), None)
        source = owner or meta.get("name", "?")
        detail = failed.get("message") or "run failed"
        # What it died of, from its newest pod's log.
        pods_of_job = sorted(pods_by_job.get(meta.get("name", ""), []),
                             key=lambda p: p.get("metadata", {}).get("creationTimestamp", ""))
        if pods_of_job:
            p = pods_of_job[-1]
            try:
                containers = p.get("spec", {}).get("containers", []) or [{}]
                cause = last_exception(read_log(client, namespace, p["metadata"]["name"],
                                                containers[0].get("name", "")))
                if cause:
                    detail = cause
            except Exception as exc:
                logger.warning("Could not read log of failed job %s: %s", meta.get("name"), exc)
        _add(incidents, "failed run", source, failed.get("reason") or "Failed", detail)
    return incidents


# ── Rendering ─────────────────────────────────────────────────────────────────

def render(entries: "OrderedDict[tuple, Entry]", scanned: int, unreadable: list[str],
           incidents: "OrderedDict[tuple, Incident] | None" = None) -> tuple[str, str]:
    incidents = incidents or OrderedDict()
    total = sum(e.count for e in entries.values())
    kinds = len(entries)
    runs = sum(i.count for i in incidents.values())
    subject = (
        f"Job Radar {settings.environment}: "
        f"{kinds} error type{'s' if kinds != 1 else ''}, "
        f"{total} error{'s' if total != 1 else ''} (last hour)"
    )
    if runs:
        subject += f", {runs} failed run{'s' if runs != 1 else ''}/kill{'s' if runs != 1 else ''}"

    by_service: "OrderedDict[str, list[Entry]]" = OrderedDict()
    for e in sorted(entries.values(), key=lambda e: e.count, reverse=True):
        by_service.setdefault(e.service, []).append(e)

    lines = [
        f"{total} error lines in {kinds} distinct fault(s), "
        f"{WINDOW_SECONDS // 60} minutes to "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC.",
        "",
    ]
    if incidents:
        # First: these are invisible in the logs, and an OOM kill is often the
        # cause of the errors listed below it.
        lines.append("── failed runs & killed containers " + "─" * 22)
        for i in sorted(incidents.values(), key=lambda i: i.count, reverse=True):
            lines.append(f"  [{i.count}×] {i.kind}: {i.source} — {i.reason}")
            lines.append(f"    {i.detail}")
            lines.append("")
        lines.append("")
    for service, items in by_service.items():
        lines.append(f"── {service} " + "─" * max(0, 56 - len(service)))
        for e in items:
            lines.append(f"  [{e.count}×] {e.level} {e.logger}")
            lines.append(f"    {e.example}")
            for tb in e.traceback[-6:]:
                lines.append(f"    | {tb}")
            lines.append("")
        lines.append("")

    lines += [
        "─" * 60,
        f"Scanned {scanned} container(s) over the last {WINDOW_SECONDS // 60} minutes.",
    ]
    if unreadable:
        lines.append(f"Could NOT read {len(unreadable)}: " + ", ".join(unreadable))
    lines += [
        "Counts are per distinct message shape; ids, numbers and quoted strings",
        "are collapsed so repeats of one fault group together.",
        "",
        "Container logs rotate, so this is best-effort rather than complete. This",
        "mail repeats every hour while an error persists — that is deliberate. If a",
        "line here isn't worth an email, fix the bug or lower its log level.",
    ]
    return subject, "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    configure_logging(settings.log_level)

    to = settings.admin_notify_email
    if not to:
        logger.warning("ADMIN_NOTIFY_EMAIL is unset — nowhere to send the digest")
        return 0

    try:
        namespace = _namespace()
        client = _kube_client()
        containers = list_containers(client, namespace)
    except Exception as exc:
        # Fail loudly. A digest that dies quietly leaves the operator believing
        # everything is fine, which is the exact failure this job exists to stop.
        logger.exception("Log digest could not list pods")
        send_email(
            to,
            f"Job Radar {settings.environment}: LOG DIGEST FAILED",
            "The hourly error digest could not read the cluster, so no errors "
            "were checked this hour.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Until this is fixed, absence of a digest means nothing.\n",
        )
        return 1

    entries: "OrderedDict[tuple, Entry]" = OrderedDict()
    unreadable: list[str] = []
    try:
        incidents = find_incidents(client, namespace)
    except Exception as exc:
        # Named in the footer, like an unreadable container — most likely the
        # batch/jobs permission is missing. The log scan still runs.
        logger.warning("Could not check for failed runs: %s", exc)
        incidents = OrderedDict()
        unreadable.append("failed-run check (jobs/pod status)")
    for pod, container, service in containers:
        try:
            collect(service, read_log(client, namespace, pod, container), entries)
        except Exception as exc:
            # One unreadable container must not cost us the other ten. It is named
            # in the footer so the digest never overstates its own coverage.
            logger.warning("Could not read logs for %s/%s: %s", pod, container, exc)
            unreadable.append(f"{pod}/{container}")

    if not entries and not unreadable and not incidents:
        # Log the quiet hour so "no email" and "job never ran" stay distinguishable.
        logger.info("Log digest: no errors in %d container(s)", len(containers))
        return 0

    subject, body = render(entries, len(containers), unreadable, incidents)
    if not entries and not incidents:
        subject = f"Job Radar {settings.environment}: log digest could not read some containers"
    send_email(to, subject, body)
    logger.info("Log digest: %d fault(s) across %d container(s) → %s",
                len(entries), len(containers), to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
