"""Agent reachability, inferred from agent_runs (app/agent_health.py).

When the network is down, the agent's run report fails too, so outages used to
leave no trace at all. The server now infers them from absence, and records
the agent spools during an outage arrive marked `late` and fill the history."""

import uuid
from datetime import datetime, timedelta, timezone

from app import agent_health as ah
from app import models
from app.security import hash_password

from .conftest import TEST_USER_ID

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
CLOUD = timedelta(minutes=15)
AGENT = {"X-User-Id": str(TEST_USER_ID)}


def _runs(n, every, last, env="local", late=False):
    return [ah.Run(last - every * i, last - every * i + timedelta(seconds=30), late, env) for i in range(n)]


# ── the rules ──────────────────────────────────────────────────

def test_no_runs_is_never_connected():
    assert ah.compute([], NOW, CLOUD).state == ah.NEVER_CONNECTED


def test_running_on_cadence_is_healthy():
    h = ah.compute(_runs(20, timedelta(minutes=15), NOW - timedelta(minutes=5)), NOW, CLOUD)
    assert h.state == ah.HEALTHY and h.unreachable_since is None
    assert h.expected_interval_minutes == 15


def test_three_missed_intervals_but_at_least_an_hour():
    """A 15-minute agent is not "unreachable" after 46 minutes — the hour minimum."""
    runs = _runs(20, timedelta(minutes=15), NOW - timedelta(minutes=50))
    assert ah.compute(runs, NOW, CLOUD).state == ah.HEALTHY
    runs = _runs(20, timedelta(minutes=15), NOW - timedelta(minutes=61))
    h = ah.compute(runs, NOW, CLOUD)
    assert h.state == ah.UNREACHABLE
    assert h.unreachable_since == NOW - timedelta(minutes=61)


def test_a_slow_local_cadence_gets_three_of_its_own_intervals():
    """An agent the user runs every 2 hours isn't late until 6 hours have passed."""
    runs = _runs(20, timedelta(hours=2), NOW - timedelta(hours=5))
    assert ah.compute(runs, NOW, CLOUD).state == ah.HEALTHY
    runs = _runs(20, timedelta(hours=2), NOW - timedelta(hours=7))
    assert ah.compute(runs, NOW, CLOUD).state == ah.UNREACHABLE


def test_hosted_users_use_the_cronjob_interval_not_their_history():
    """A hosted user's cadence is the CronJob's, whatever their runs looked like."""
    runs = _runs(20, timedelta(hours=6), NOW - timedelta(minutes=70), env="cloud")
    h = ah.compute(runs, NOW, CLOUD)
    assert h.expected_interval_minutes == 15 and h.state == ah.UNREACHABLE


def test_late_records_turn_a_silent_gap_into_a_late_period():
    """Before the flush: a silent gap. After: the same span shows as 'late'."""
    live = _runs(10, timedelta(minutes=15), NOW - timedelta(hours=5)) + [
        ah.Run(NOW - timedelta(minutes=5), NOW - timedelta(minutes=4), False, "local")]
    silent = ah.compute(live, NOW, CLOUD)
    assert [p.kind for p in silent.periods] == ["silent"]

    flushed = _runs(18, timedelta(minutes=15), NOW - timedelta(minutes=20), late=True)
    after = ah.compute(live + flushed, NOW, CLOUD)
    assert after.state == ah.HEALTHY
    assert [p.kind for p in after.periods] == ["late"]


def test_ongoing_silence_is_listed_up_to_now():
    h = ah.compute(_runs(20, timedelta(minutes=15), NOW - timedelta(hours=3)), NOW, CLOUD)
    assert h.periods[0].kind == "silent" and h.periods[0].end == NOW


# ── the API ────────────────────────────────────────────────────

def _report(client, started, late=None):
    body = {"environment": "local", "agent_version": "2.0", "status": "success",
            "started_at": started.isoformat(), "finished_at": (started + timedelta(seconds=20)).isoformat()}
    if late is not None:
        body["late"] = late
    r = client.post("/agent/runs", json=body, headers=AGENT)
    assert r.status_code == 201, r.text


def test_late_is_optional_and_stored(client, db):
    now = datetime.now(timezone.utc)
    _report(client, now - timedelta(minutes=30))
    _report(client, now - timedelta(minutes=15), late=True)
    lates = sorted(r.late for r in db.query(models.AgentRun).all())
    assert lates == [False, True]


def test_settings_stats_carry_this_users_health(client, db):
    assert client.get("/agent/stats").json()["health"]["state"] == "never_connected"
    now = datetime.now(timezone.utc)
    for i in range(5):
        _report(client, now - timedelta(minutes=15 * i + 1))
    health = client.get("/agent/stats").json()["health"]
    assert health["state"] == "healthy" and health["environment"] == "local"


def test_admin_list_is_per_user_with_unreachable_first(client, db, monkeypatch):
    """Multi-user: a hosted user has no log, so the admin list is where an
    outage shows. Non-agent users aren't listed."""
    from app.deps import get_current_admin
    from app.main import app
    app.dependency_overrides[get_current_admin] = lambda: db.get(models.User, TEST_USER_ID)

    stale = uuid.uuid4()
    db.add(models.User(id=stale, email="stale@example.com", password_hash=hash_password("x"),
                       full_name="S", is_approved=True, is_admin=False))
    db.add(models.User(id=uuid.uuid4(), email="not-an-agent-user@example.com",
                       password_hash=hash_password("x"), full_name="N", is_approved=True, is_admin=False))
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db.add(models.AgentRun(user_id=stale, environment=models.AgentEnvironment.LOCAL, agent_version="2",
                           status=models.AgentRunStatus.SUCCESS, started_at=old, finished_at=old))
    db.commit()
    _report(client, datetime.now(timezone.utc) - timedelta(minutes=2))

    rows = client.get("/agent/health/users").json()
    assert [(r["email"], r["state"]) for r in rows][0] == ("stale@example.com", "unreachable")
    assert "not-an-agent-user@example.com" not in {r["email"] for r in rows}
