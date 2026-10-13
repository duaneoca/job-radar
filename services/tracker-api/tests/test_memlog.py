"""Per-route memory accounting: the evidence for finding tracker-api's growth."""

from app import memlog


def test_report_names_the_routes_that_grew(monkeypatch, caplog):
    monkeypatch.setattr(memlog, "rss_kb", lambda: 300 * 1024)
    acct = memlog.RouteMemory(report_every=10_000)
    acct.start_kb = 250 * 1024
    for _ in range(3):
        acct.record("POST /jobs/{job_id}/ai-review", 4096)
    acct.record("GET /jobs", -1024)
    line = acct.report()
    assert "rss=300Mi since_start=+50Mi requests=4" in line
    assert line.index("POST /jobs/{job_id}/ai-review +12.0Mi/3req max+4.0Mi") < line.index("GET /jobs")
    assert not acct.window                          # each report covers its own window


def test_reports_on_schedule(monkeypatch, caplog):
    monkeypatch.setattr(memlog, "rss_kb", lambda: 1024)
    acct = memlog.RouteMemory(report_every=2)
    with caplog.at_level("INFO", logger="app.memlog"):
        acct.record("GET /a", 0)
        assert not caplog.records
        acct.record("GET /a", 0)
    assert caplog.records and caplog.records[0].levelname == "INFO"   # never the ERROR digest


def test_middleware_records_the_route_template(client, monkeypatch):
    """Grouping by template is what makes one route's growth visible."""
    seen = []
    monkeypatch.setattr(memlog, "rss_kb", lambda: 1000)
    monkeypatch.setattr(memlog, "_account", lambda: type("A", (), {"record": lambda self, r, d: seen.append((r, d))})())
    client.get("/jobs/00000000-0000-0000-0000-000000000099")
    assert ("GET /jobs/{review_id}", 0) in seen


def test_no_proc_no_accounting(client, monkeypatch):
    """Off Linux there is nothing to measure; requests must still work."""
    monkeypatch.setattr(memlog, "rss_kb", lambda: None)
    assert client.get("/health").status_code == 200
