"""Smoke tests for the ops surface: /health, /status, /metrics, /."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cil import __version__


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "cil"
    assert body["version"] == __version__


def test_status_reports_runtime(client: TestClient) -> None:
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == __version__
    assert body["env"] == "local"
    assert body["uptime_seconds"] >= 0


def test_metrics_scrape(client: TestClient) -> None:
    # Generate at least one request so a counter sample exists.
    client.get("/health")
    # follow_redirects=False: /metrics must be a direct 200, not a 307 to /metrics/.
    resp = client.get("/metrics", follow_redirects=False)
    assert resp.status_code == 200
    assert "cil_http_requests_total" in resp.text
    assert 'path="/health"' in resp.text


def test_root_banner(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "cil"
