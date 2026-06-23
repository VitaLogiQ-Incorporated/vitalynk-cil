"""Tests for the telemetry stores: roundtrip, ordering, filtering, durability."""

from __future__ import annotations

from pathlib import Path

import pytest

from cil.storage.interface import TelemetryStore
from cil.storage.memory import InMemoryTelemetryStore
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.telemetry.scenarios import Scenario
from cil.telemetry.simulator import SimulatorAdapter


async def _make_samples(n: int, *, scenario: Scenario = Scenario.HEALTHY) -> list:
    sim = SimulatorAdapter(seed=99)
    sim.set_scenario(scenario, duration=max(1, n))
    return [await sim.sample() for _ in range(n)]


async def test_memory_store_is_a_telemetry_store() -> None:
    assert isinstance(InMemoryTelemetryStore(), TelemetryStore)


async def test_sqlite_store_is_a_telemetry_store() -> None:
    assert isinstance(SQLiteTelemetryStore(":memory:"), TelemetryStore)


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_write_count_roundtrip(store_kind: str) -> None:
    store: TelemetryStore = (
        InMemoryTelemetryStore() if store_kind == "memory" else SQLiteTelemetryStore(":memory:")
    )
    await store.setup()
    samples = await _make_samples(5)

    written = await store.write_samples(samples)
    assert written == 5
    assert await store.count() == 5

    read_back = await store.read_samples(limit=100)
    assert read_back == samples  # exact value roundtrip, oldest-first
    await store.close()


async def test_read_limit_returns_most_recent() -> None:
    store = SQLiteTelemetryStore(":memory:")
    await store.setup()
    samples = await _make_samples(10)
    await store.write_samples(samples)

    recent = await store.read_samples(limit=3)
    assert recent == samples[-3:]
    await store.close()


async def test_path_filter() -> None:
    store = InMemoryTelemetryStore()
    await store.setup()
    # failover scenario produces samples on both modem-a and modem-b
    samples = await _make_samples(10, scenario=Scenario.DUAL_MODEM_FAILOVER)
    await store.write_samples(samples)

    a = await store.read_samples(path_id="modem-a", limit=100)
    b = await store.read_samples(path_id="modem-b", limit=100)
    assert a and b
    assert all(s.path_id == "modem-a" for s in a)
    assert all(s.path_id == "modem-b" for s in b)
    assert len(a) + len(b) == 10


async def test_sqlite_survives_reboot(tmp_path: Path) -> None:
    db = str(tmp_path / "telemetry.db")
    samples = await _make_samples(7)

    store1 = SQLiteTelemetryStore(db)
    await store1.setup()
    await store1.write_samples(samples)
    await store1.close()

    # Re-open the same file => data persists (reboot-safe).
    store2 = SQLiteTelemetryStore(db)
    await store2.setup()
    assert await store2.count() == 7
    assert await store2.read_samples(limit=100) == samples
    await store2.close()
