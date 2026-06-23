"""End-to-end: the running app monitors clinical endpoints and serves their health."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cil.api.app import create_app
from cil.config import Settings


@pytest.fixture
def clinical_client() -> Iterator[TestClient]:
    settings = Settings(
        telemetry_enabled=False,
        app_monitoring_enabled=True,
        telemetry_db_path=":memory:",
        app_monitoring_interval_s=0.01,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _wait_for_health(client: TestClient, *, timeout_s: float = 5.0) -> list:
    deadline = time.monotonic() + timeout_s
    health: list = []
    while time.monotonic() < deadline:
        health = client.get("/clinical/health").json()
        if health:
            return health
        time.sleep(0.05)
    return health


def test_endpoints_listed(clinical_client: TestClient) -> None:
    endpoints = clinical_client.get("/clinical/endpoints").json()
    assert len(endpoints) == 5
    systems = {e["system"] for e in endpoints}
    assert {"Epic", "Cerner", "PACS", "RIS", "OR"} <= systems


def test_app_serves_clinical_health(clinical_client: TestClient) -> None:
    health = _wait_for_health(clinical_client)
    assert health, "expected clinical health to populate"
    names = {h["endpoint"] for h in health}
    assert "epic-ehr" in names
    sample = next(h for h in health if h["endpoint"] == "epic-ehr")
    assert "reachable" in sample and "live" in sample and "healthy" in sample


def test_clinical_metrics_exposed(clinical_client: TestClient) -> None:
    _wait_for_health(clinical_client)
    metrics = clinical_client.get("/metrics").text
    assert "cil_app_live" in metrics
