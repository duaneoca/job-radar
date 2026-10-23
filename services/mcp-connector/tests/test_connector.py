"""The connector end to end inside one process: real Starlette app and MCP SDK,
tracker-api replaced by an httpx MockTransport. (A two-server run with the real
tracker-api and OAuth flow was done by hand for the PR; this pins the contract.)"""

import json

import httpx
import pytest
from starlette.testclient import TestClient

from app import api, main

GOOD = "jr_at_good"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-06-18"}

JOB = {"id": "11111111-1111-1111-1111-111111111111", "title": "Principal AI Engineer", "company": "ServiceNow",
       "location": "Santa Clara, CA", "remote": False, "salary_min": 200000, "salary_max": 250000,
       "ai_score": 9.1, "recommended": True, "status": "reviewed", "starred": False,
       "date_posted": "2026-10-20T00:00:00Z", "url": "https://example.com/1", "description": "Build agents.",
       "ai_summary": "Your background fits.", "notes": None, "research_summary": None, "timeline": []}


class Tracker:
    """Fake tracker-api: records requests, answers per path."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.reply: dict[tuple[str, str], tuple[int, dict]] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.headers.get("authorization") != f"Bearer {GOOD}":
            return httpx.Response(401, json={"detail": "Invalid or expired token"})
        code, body = self.reply.get((req.method, req.url.path), (200, {}))
        return httpx.Response(code, json=body)


@pytest.fixture
def tracker(monkeypatch):
    t = Tracker()
    t.reply[("GET", "/auth/me")] = (200, {"email": "d@example.com"})
    monkeypatch.setattr(api, "_client", httpx.AsyncClient(base_url="http://tracker", transport=httpx.MockTransport(t)))
    main._valid.clear()
    return t


@pytest.fixture(scope="module")
def _app():
    # One app start for the file: the SDK's session manager runs once per process.
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def client(_app, tracker):
    return _app


def _rpc(client, method, params=None, token=GOOD, rid=1, headers=None):
    h = {**MCP_HEADERS, **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
    return client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def _tool(client, name, args):
    r = _rpc(client, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    text = res["content"][0]["text"]
    return res.get("isError", False), (json.loads(text) if not res.get("isError") else text)


# ── discovery and sign-in ─────────────────────────────────────

def test_protected_resource_metadata(client):
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        m = client.get(path).json()
        assert m["resource"] == "http://testserver/mcp"          # exactly the URL users paste
        assert m["authorization_servers"] == ["http://testserver"]
        assert set(m["scopes_supported"]) == {"jobs:read", "jobs:write", "generate"}


@pytest.mark.parametrize("token", [None, "something-else"])
def test_no_or_foreign_token_gets_the_sign_in_challenge(client, token):
    r = _rpc(client, "initialize", token=token)
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer ") and 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' in www


def test_a_dead_token_is_invalid_token(client):
    r = _rpc(client, "initialize", token="jr_at_revoked")
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]


def test_a_checked_token_is_cached_briefly(client, tracker):
    _rpc(client, "tools/list")
    _rpc(client, "tools/list")
    assert sum(1 for c in tracker.calls if c.url.path == "/auth/me") == 1


def test_a_foreign_host_is_refused(client):
    r = _rpc(client, "tools/list", headers={"Host": "evil.example.com"})
    assert r.status_code in (400, 403, 421)


def test_health_needs_no_token(client):
    assert client.get("/health").json()["status"] == "ok"


# ── tools ─────────────────────────────────────────────────────

def test_tools_are_listed_with_their_annotations(client):
    tools = {t["name"]: t for t in _rpc(client, "tools/list").json()["result"]["tools"]}
    assert len(tools) == 9
    assert tools["list_jobs"]["annotations"]["readOnlyHint"] is True
    assert tools["dismiss_job"]["annotations"]["destructiveHint"] is True
    assert tools["mark_job_expired"]["annotations"]["destructiveHint"] is True
    assert "applied" not in json.dumps(tools["star_job"])     # no tool sets other statuses


def test_list_jobs_forwards_filters_and_the_token(client, tracker):
    tracker.reply[("GET", "/jobs")] = (200, {"total": 1, "items": [JOB]})
    err, out = _tool(client, "list_jobs", {"status": ["reviewed"], "starred": False, "limit": 5})
    assert not err and out["total"] == 1
    j = out["jobs"][0]
    assert j["salary"] == "$200,000–$250,000" and j["job_radar_url"] == f"http://testserver/jobs/{JOB['id']}"
    req = next(c for c in tracker.calls if c.url.path == "/jobs")
    assert req.headers["authorization"] == f"Bearer {GOOD}"
    assert req.url.params.get_list("status") == ["reviewed"] and req.url.params["starred"] == "false"
    assert req.url.params["limit"] == "5"


def test_get_job_flags_untrusted_text_and_caps_the_description(client, tracker):
    tracker.reply[("GET", f"/jobs/{JOB['id']}")] = (200, {**JOB, "description": "x" * 20_000})
    err, out = _tool(client, "get_job", {"job_id": JOB["id"]})
    assert not err and out["description"].endswith("…(truncated)") and len(out["description"]) < 8100
    assert "untrusted_content" in out


def test_dismiss_sends_the_status_and_reason(client, tracker):
    tracker.reply[("PATCH", f"/jobs/{JOB['id']}")] = (200, {**JOB, "status": "dismissed"})
    err, out = _tool(client, "dismiss_job", {"job_id": JOB["id"], "reason": "Analyst role"})
    assert not err and out["status"] == "dismissed"
    body = json.loads(next(c for c in tracker.calls if c.method == "PATCH").content)
    assert body == {"status": "dismissed", "status_reason": "Analyst role"}


def test_tracker_api_messages_reach_the_assistant(client, tracker):
    tracker.reply[("POST", f"/jobs/{JOB['id']}/research")] = (
        429, {"detail": "Daily limit reached: connected apps can start 20 company research runs per day on this account."})
    err, text = _tool(client, "start_company_research", {"job_id": JOB["id"]})
    assert err and "Daily limit reached" in text


def test_tracker_api_down_is_a_clear_error(client, monkeypatch):
    def boom(req):
        if req.url.path == "/auth/me":
            return httpx.Response(200, json={})
        raise httpx.ConnectError("down")
    monkeypatch.setattr(api, "_client", httpx.AsyncClient(base_url="http://tracker", transport=httpx.MockTransport(boom)))
    main._valid.clear()
    err, text = _tool(client, "get_run_status", {"job_id": JOB["id"]})
    assert err and "isn't responding" in text


# ── tool-call logging and guidance ────────────────────────────

def test_every_tool_call_is_logged_without_its_arguments(client, tracker, caplog):
    import logging
    tracker.reply[("POST", f"/jobs/{JOB['id']}/notes")] = (200, {**JOB, "notes": "x"})
    with caplog.at_level(logging.INFO, logger="app.tools"):
        _tool(client, "add_note", {"job_id": JOB["id"], "text": "SECRET-NOTE-TEXT about salary"})
    lines = [r.getMessage() for r in caplog.records if r.name == "app.tools"]
    assert any(line.startswith(f"tool=add_note job={JOB['id']} ok ") for line in lines), lines
    assert not any("SECRET-NOTE-TEXT" in r.getMessage() for r in caplog.records)


def test_a_refused_call_is_logged_at_info_not_error(client, tracker, caplog):
    import logging
    tracker.reply[("POST", f"/jobs/{JOB['id']}/research")] = (429, {"detail": "Daily limit reached: …"})
    with caplog.at_level(logging.INFO, logger="app.tools"):
        _tool(client, "start_company_research", {"job_id": JOB["id"]})
    recs = [r for r in caplog.records if r.name == "app.tools"]
    assert recs and all(r.levelno == logging.INFO for r in recs)       # never the operator's ERROR digest
    assert "refused" in recs[-1].getMessage() and "Daily limit" in recs[-1].getMessage()


def test_logs_use_the_shared_format_the_digest_parses():
    """In a fresh interpreter, as the pod starts: importing the app must leave the
    shared format on the root logger — not FastMCP's bare one (it calls
    basicConfig when constructed). pytest manages root handlers in-process, so
    this checks a subprocess."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import logging, app.main; logging.getLogger('app.tools').info('tool=list_jobs ok 1ms')"],
        capture_output=True, text=True, check=True, cwd=str(__import__('pathlib').Path(__file__).parents[1]),
    ).stderr.strip().splitlines()[-1]
    import re
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO app\.tools: tool=list_jobs ok 1ms$", out), out


def test_instructions_steer_toward_job_radars_own_work(client):
    r = _rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}})
    text = r.json()["result"]["instructions"]
    assert "lead with Job\n  Radar's AI review" in text or "lead with Job Radar's AI review" in text.replace("\n  ", " ")
    assert "start_company_research" in text and "Don't search the web" in text
    assert "ask which one" in text
