"""Tests for the live Ericsson adapter (CIL-201) — brain-side, transport mocked.

The NetCloudClient (real HTTP transport) is ops/EPIC-07's; here a fake client
proves the adapter's fetch->normalize path produces a valid TelemetrySample,
plus the FR-101 acceptance (normalized data available downstream <= 2s).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest

from cil.storage.memory import InMemoryTelemetryStore
from cil.telemetry.adapter import TelemetryAdapter
from cil.telemetry.ericsson import EricssonNetCloudAdapter, NetCloudClient
from cil.telemetry.schema import TelemetrySample

_RAW: dict[str, Any] = {
    "timestamp": "2026-01-01T00:00:00+00:00",
    "path_id": "modem-a",
    "carrier": "Verizon",
    "profile": "primary",
    "rssi": -65,
    "rsrp": -90,
    "rsrq": -10,
    "sinr": 15,
    "latency_ms": 25,
    "packet_loss_pct": 0.1,
    "jitter_ms": 2,
    "throughput_mbps": 85,
    "dns_response_ms": 15,
    "reachable": True,
    "cpu_pct": 20,
    "mem_pct": 45,
    "uptime_s": 3600,
}


class _FakeNetCloudClient:
    """Stand-in for the EPIC-07 transport."""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self._raw = raw

    async def fetch_raw(self) -> Mapping[str, Any]:
        return self._raw

    async def list_paths(self) -> list[str]:
        return ["modem-a", "modem-b"]


def test_fake_client_satisfies_netcloud_client() -> None:
    assert isinstance(_FakeNetCloudClient(_RAW), NetCloudClient)


def test_adapter_satisfies_telemetry_adapter() -> None:
    assert isinstance(EricssonNetCloudAdapter(), TelemetryAdapter)


async def test_sample_fetches_and_normalizes() -> None:
    adapter = EricssonNetCloudAdapter(_FakeNetCloudClient(_RAW))
    sample = await adapter.sample()
    assert isinstance(sample, TelemetrySample)
    assert sample.carrier == "Verizon"
    assert sample.network.latency_ms == 25.0
    assert sample.network.reachable is True


async def test_list_paths_delegates_to_client() -> None:
    adapter = EricssonNetCloudAdapter(_FakeNetCloudClient(_RAW))
    assert await adapter.list_paths() == ["modem-a", "modem-b"]


async def test_sample_without_client_points_to_epic07() -> None:
    adapter = EricssonNetCloudAdapter()
    with pytest.raises(NotImplementedError, match="EPIC-07"):
        await adapter.sample()


async def test_fr101_normalized_sample_available_within_2s() -> None:
    # FR-101 acceptance: normalized data available downstream <= 2s.
    adapter = EricssonNetCloudAdapter(_FakeNetCloudClient(_RAW))
    store = InMemoryTelemetryStore()
    await store.setup()

    start = time.perf_counter()
    sample = await adapter.sample()
    await store.write_sample(sample)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert await store.count() == 1
