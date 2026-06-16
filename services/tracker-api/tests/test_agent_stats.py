"""Regression test for /agent/stats — last_run serialization (AgentRun.id -> run_id)."""

import uuid
from datetime import datetime, timezone

from app import models
from app.routers.agent import _compute_stats

from .conftest import TEST_USER_ID


def test_stats_with_a_run_does_not_raise(db, test_user):
    """With at least one agent run, _compute_stats must serialize last_run (the
    AgentRun PK is `id`, the schema field is `run_id`)."""
    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4()
    db.add(models.AgentRun(
        id=run_id, user_id=TEST_USER_ID,
        environment=models.AgentEnvironment.LOCAL, agent_version="0.1.0",
        status=models.AgentRunStatus.SUCCESS, started_at=now, finished_at=now,
        emails_processed=3,
    ))
    db.commit()

    stats = _compute_stats(TEST_USER_ID, db)
    assert stats.last_run is not None
    assert stats.last_run.run_id == run_id
    assert stats.last_run.emails_processed == 3
    assert stats.last_run.status == models.AgentRunStatus.SUCCESS


def test_stats_no_runs_last_run_is_none(db, test_user):
    stats = _compute_stats(TEST_USER_ID, db)
    assert stats.last_run is None


def test_global_stats_does_not_raise(db, test_user):
    now = datetime.now(timezone.utc)
    db.add(models.AgentRun(
        id=uuid.uuid4(), user_id=TEST_USER_ID,
        environment=models.AgentEnvironment.CLOUD, agent_version="0.1.0",
        status=models.AgentRunStatus.PARTIAL, started_at=now, finished_at=now,
        emails_processed=5,
    ))
    db.commit()
    stats = _compute_stats(None, db)   # global / admin path
    assert stats.last_run is not None
    assert stats.last_run.emails_processed == 5
