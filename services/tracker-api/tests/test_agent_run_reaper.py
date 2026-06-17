"""Reaper for dangling agent_runs (§1.6b) — runs left finished_at=NULL by a hard
SIGKILL/OOM get marked failed in the nightly cleanup pass."""

import uuid
from datetime import datetime, timedelta, timezone

from app import models
from app.routers.admin import _do_cleanup

from .conftest import TEST_USER_ID


def _run(db, *, started_minutes_ago, finished=False, status=models.AgentRunStatus.SUCCESS):
    started = datetime.now(timezone.utc) - timedelta(minutes=started_minutes_ago)
    run = models.AgentRun(
        id=uuid.uuid4(), user_id=TEST_USER_ID,
        environment=models.AgentEnvironment.CLOUD, agent_version="0.1.0",
        status=status, started_at=started,
        finished_at=started if finished else None,
    )
    db.add(run)
    db.commit()
    return run.id


def test_reaps_old_dangling_run(db, test_user):
    rid = _run(db, started_minutes_ago=60)            # NULL-finished, well past deadline
    _do_cleanup(db)
    run = db.query(models.AgentRun).get(rid)
    assert run.status == models.AgentRunStatus.FAILED
    assert run.finished_at == run.started_at
    assert "reaped" in (run.error_summary or "")


def test_leaves_recent_unfinished_run(db, test_user):
    rid = _run(db, started_minutes_ago=5)             # still within the run deadline
    _do_cleanup(db)
    run = db.query(models.AgentRun).get(rid)
    assert run.finished_at is None
    assert run.status == models.AgentRunStatus.SUCCESS


def test_leaves_finished_run_untouched(db, test_user):
    rid = _run(db, started_minutes_ago=60, finished=True, status=models.AgentRunStatus.SUCCESS)
    _do_cleanup(db)
    run = db.query(models.AgentRun).get(rid)
    assert run.status == models.AgentRunStatus.SUCCESS
    assert run.error_summary is None
