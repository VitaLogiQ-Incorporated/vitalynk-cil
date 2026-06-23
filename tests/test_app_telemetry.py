"""End-to-end: the running app ingests simulated telemetry and serves it."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cil.api.app import create_app
from cil.config import Settings


@pytest.fixture
def telemetry_client() -> Iterator[TestClient]:
    # In-memory DB + fast cadence so the background loop fills quickly.
    settings = Settings(
        telemetry_enabled=True,
        telemetry_db_path=":memory:",
        telemetry_interval_s=0.01,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _wait_for_samples(client: TestClient, *, minimum: int = 1, timeout_s: float = 5.0) -> int:
    deadline = time.monotonic() + timeout_s
    count = 0
    while time.monotonic() < deadline:
        count = client.get("/telemetry/count").json()["count"]
        if count >= minimum:
            return count
        time.sleep(0.05)
    return count


def test_app_ingests_and_serves_telemetry(telemetry_client: TestClient) -> None:
    count = _wait_for_samples(telemetry_client, minimum=3)
    assert count >= 3

    latest = telemetry_client.get("/telemetry/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert "network" in body
    assert body["path_id"] == "modem-a"

    recent = telemetry_client.get("/telemetry/recent?limit=5")
    assert recent.status_code == 200
    assert len(recent.json()) >= 1


def test_metrics_expose_telemetry(telemetry_client: TestClient) -> None:
    _wait_for_samples(telemetry_client, minimum=2)
    metrics = telemetry_client.get("/metrics").text
    assert "cil_telemetry_samples_total" in metrics
