"""Tests for the scrape-on-criteria-change trigger + debounce (Phase 5)."""

from unittest.mock import patch

from app.routers import criteria


# ── Debounce helper ───────────────────────────────────────────

def test_debounce_enqueues_when_window_clear():
    with patch.object(criteria, "_redis_client") as r, patch.object(criteria, "_celery") as cel:
        r.set.return_value = True   # NX succeeded → first save in window
        criteria._maybe_enqueue_scrape("u1")
    cel.send_task.assert_called_once()
    assert cel.send_task.call_args.args[0] == "app.tasks.scrape_user"


def test_debounce_skips_within_window():
    with patch.object(criteria, "_redis_client") as r, patch.object(criteria, "_celery") as cel:
        r.set.return_value = None   # key already set → recent scrape
        criteria._maybe_enqueue_scrape("u1")
    cel.send_task.assert_not_called()


def test_enqueue_never_raises_on_redis_failure():
    with patch.object(criteria, "_redis_client") as r, patch.object(criteria, "_celery"):
        r.set.side_effect = ConnectionError("redis down")
        # Must not raise — best-effort.
        criteria._maybe_enqueue_scrape("u1")


# ── Endpoint wiring ───────────────────────────────────────────

def test_upsert_criteria_triggers_scrape(client):
    with patch("app.routers.criteria._maybe_enqueue_scrape") as enq:
        resp = client.put("/criteria", json={"job_titles": ["Engineer"], "search_locations": ["Remote"]})
    assert resp.status_code == 200
    enq.assert_called_once()


def test_patch_criteria_triggers_scrape(client):
    with patch("app.routers.criteria._maybe_enqueue_scrape"):
        cid = client.put("/criteria", json={"job_titles": ["Engineer"]}).json()["id"]
    with patch("app.routers.criteria._maybe_enqueue_scrape") as enq:
        resp = client.patch(f"/criteria/{cid}", json={"job_titles": ["Staff Engineer"]})
    assert resp.status_code == 200
    enq.assert_called_once()
