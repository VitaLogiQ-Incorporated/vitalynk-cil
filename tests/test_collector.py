"""Tests for the WAN-monitoring collector (simulator -> normalize -> store)."""

from __future__ import annotations

from prometheus_client import REGISTRY

from cil.storage.memory import InMemoryTelemetryStore
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.telemetry.monitor import TelemetryCollector
from cil.telemetry.simulator import SimulatorAdapter


async def test_run_once_persists_and_tracks_latest() -> None:
    store = InMemoryTelemetryStore()
    await store.setup()
    collector = TelemetryCollector(SimulatorAdapter(seed=1), store)

    sample = await collector.run_once()
    assert collector.latest == sample
    assert await store.count() == 1


async def test_run_loop_ingests_n_samples() -> None:
    store = InMemoryTelemetryStore()
    await store.setup()
    collector = TelemetryCollector(SimulatorAdapter(seed=2), store, interval_s=0.0)

    ingested = await collector.run(max_samples=5)
    assert ingested == 5
    assert await store.count() == 5


async def test_end_to_end_simulator_to_sqlite() -> None:
    store = SQLiteTelemetryStore(":memory:")
    await store.setup()
    collector = TelemetryCollector(SimulatorAdapter(seed=3), store, interval_s=0.0)

    await collector.run(max_samples=8)
    assert await store.count() == 8
    rows = await store.read_samples(limit=100)
    assert len(rows) == 8
    assert rows[0].network.reachable is True
    await store.close()


async def test_metrics_increment() -> None:
    store = InMemoryTelemetryStore()
    await store.setup()
    collector = TelemetryCollector(SimulatorAdapter(seed=4), store, interval_s=0.0)

    def total() -> float:
        return (
            REGISTRY.get_sample_value("cil_telemetry_samples_total", {"path_id": "modem-a"}) or 0.0
        )

    before = total()
    await collector.run(max_samples=4)  # healthy stays on modem-a
    after = total()
    assert after - before == 4
